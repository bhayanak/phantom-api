#!/usr/bin/env bash
# Exercise a running vCenter mock the way an inventory collector would:
# handshake, log in, read inventory, log out.
#
#   ./smoke.sh                        against https://localhost:8443
#   ./smoke.sh https://localhost:443  against another address
#
# Exits non-zero if any step fails or returns a SOAP fault.

set -uo pipefail

BASE="${1:-https://localhost:8443}"
COOKIES="$(mktemp)"
trap 'rm -f "$COOKIES"' EXIT

pass=0
fail=0

soap() {
    # $1 = path, $2 = body of <soapenv:Body>
    curl -sk --max-time 20 -c "$COOKIES" -b "$COOKIES" \
        -X POST "$BASE$1" \
        -H 'Content-Type: text/xml; charset=utf-8' \
        -H 'SOAPAction: urn:vim25/8.0.2.0' \
        --data "<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\"
  xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\" xmlns:urn=\"urn:vim25\">
<soapenv:Body>$2</soapenv:Body></soapenv:Envelope>"
}

# $1 = label, $2 = response, $3 = string the response must contain
check() {
    if [[ "$2" == *"soapenv:Fault"* ]]; then
        printf '  FAIL  %-34s SOAP fault\n' "$1"
        fail=$((fail + 1))
    elif [[ "$2" == *"$3"* ]]; then
        printf '  ok    %-34s\n' "$1"
        pass=$((pass + 1))
    else
        printf '  FAIL  %-34s no %s in response\n' "$1" "$3"
        fail=$((fail + 1))
    fi
}

echo "vCenter mock smoke test -> $BASE"
echo

r=$(soap /sdk '<urn:RetrieveServiceContent><urn:_this type="ServiceInstance">ServiceInstance</urn:_this></urn:RetrieveServiceContent>')
check "RetrieveServiceContent" "$r" "RetrieveServiceContentResponse"

r=$(soap /sdk '<urn:Login><urn:_this type="SessionManager">SessionManager</urn:_this><urn:userName>administrator@vsphere.local</urn:userName><urn:password>any</urn:password></urn:Login>')
check "Login" "$r" "LoginResponse"

for kind in VirtualMachine HostSystem Datastore; do
    r=$(soap /sdk "<urn:CreateContainerView><urn:_this type=\"ViewManager\">ViewManager</urn:_this><urn:container type=\"Folder\">group-d1</urn:container><urn:type>$kind</urn:type><urn:recursive>true</urn:recursive></urn:CreateContainerView>")
    check "CreateContainerView $kind" "$r" "CreateContainerViewResponse"
done

r=$(soap /sdk '<urn:RetrieveProperties><urn:_this type="PropertyCollector">propertyCollector</urn:_this><urn:specSet><urn:propSet><urn:type>VirtualMachine</urn:type><urn:pathSet>name</urn:pathSet></urn:propSet><urn:objectSet><urn:obj type="Folder">group-d1</urn:obj></urn:objectSet></urn:specSet></urn:RetrieveProperties>')
check "RetrieveProperties VM" "$r" "RetrievePropertiesResponse"

r=$(soap /sdk '<urn:Logout><urn:_this type="SessionManager">SessionManager</urn:_this></urn:Logout>')
check "Logout" "$r" "LogoutResponse"

code=$(curl -sk --max-time 20 -o /dev/null -w '%{http_code}' "$BASE/__phantom/status")
if [[ "$code" == "200" ]]; then
    printf '  ok    %-34s\n' "control plane"
    pass=$((pass + 1))
elif [[ "$code" == "403" ]]; then
    printf '  skip  %-34s loopback-only (set control_plane.bind: 0.0.0.0)\n' "control plane"
else
    printf '  FAIL  %-34s HTTP %s\n' "control plane" "$code"
    fail=$((fail + 1))
fi

echo
echo "  $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
