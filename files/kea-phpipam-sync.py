#!/usr/bin/python3
"""Sync phpIPAM addresses that carry a MAC address into Kea reservations.

Reads addresses from the phpIPAM REST API and reconciles them with the
reservations Kea holds in its hosts database, using the Kea Control Agent
API and the host_cmds hook (reservation-get-all / -add / -del).

Configuration is a JSON file (default /etc/kea/kea-phpipam-sync.json)
written by the ansible-role-kea_dhcp role.
"""

import argparse
import base64
import json
import logging
import ssl
import sys
import urllib.error
import urllib.request

LOG = logging.getLogger("kea-phpipam-sync")


def normalise_mac(mac):
    mac = (mac or "").strip().lower().replace("-", ":").replace(".", "")
    if not mac:
        return ""
    if ":" not in mac and len(mac) == 12:
        mac = ":".join(mac[i:i + 2] for i in range(0, 12, 2))
    return mac


def http_json(url, payload=None, headers=None, verify=True):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    ctx = None
    if url.startswith("https://") and not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors="replace")
        try:
            return json.loads(body)
        except ValueError:
            raise RuntimeError(f"{url}: HTTP {err.code}: {body[:200]}") from err


class Kea:
    def __init__(self, cfg):
        self.url = cfg["kea_api_url"]
        self.headers = {}
        if cfg.get("kea_api_user"):
            token = f"{cfg['kea_api_user']}:{cfg['kea_api_password']}"
            self.headers["Authorization"] = "Basic " + base64.b64encode(token.encode()).decode()
        self.verify = cfg.get("kea_api_verify_tls", True)

    def command(self, service, command, arguments):
        payload = {"command": command, "service": [service], "arguments": arguments}
        answer = http_json(self.url, payload, self.headers, self.verify)
        if isinstance(answer, list):
            answer = answer[0]
        return answer

    def reservations(self, service, subnet_id):
        hosts, source_index, from_host_id = [], 0, 0
        while True:
            args = {"subnet-id": subnet_id, "limit": 1000}
            if source_index or from_host_id:
                args.update({"source-index": source_index, "from": from_host_id})
            answer = self.command(service, "reservation-get-page", args)
            if answer.get("result") == 3:  # empty: no more pages
                return hosts
            if answer.get("result") != 0:
                raise RuntimeError(f"reservation-get-page failed: {answer.get('text')}")
            arguments = answer.get("arguments", {})
            hosts.extend(arguments.get("hosts", []))
            nxt = arguments.get("next", {})
            source_index, from_host_id = nxt.get("source-index", 0), nxt.get("from", 0)
            if not arguments.get("hosts"):
                return hosts


def phpipam_addresses(cfg, subnet_id):
    url = f"{cfg['phpipam_url'].rstrip('/')}/api/{cfg['phpipam_app_id']}/subnets/{subnet_id}/addresses/"
    answer = http_json(url, headers={"token": cfg["phpipam_token"]}, verify=cfg.get("phpipam_verify_tls", True))
    if not answer.get("success"):
        if answer.get("code") == 404:
            return []
        raise RuntimeError(f"phpIPAM subnet {subnet_id}: {answer.get('message')}")
    return answer.get("data") or []


def desired_reservations(cfg, mapping):
    family = int(mapping.get("family", 4))
    wanted = {}
    for addr in phpipam_addresses(cfg, mapping["phpipam_subnet_id"]):
        mac = normalise_mac(addr.get("mac"))
        if not mac or not addr.get("ip"):
            continue
        res = {"subnet-id": int(mapping["kea_subnet_id"]), "hw-address": mac}
        if family == 4:
            res["ip-address"] = addr["ip"]
        else:
            res["ip-addresses"] = [addr["ip"]]
        if addr.get("hostname"):
            res["hostname"] = addr["hostname"].strip().lower()
        wanted[mac] = res
    return wanted


def comparable(host, family):
    keys = ["hw-address", "hostname"] + (["ip-address"] if family == 4 else ["ip-addresses"])
    return {k: host.get(k) for k in keys if host.get(k) not in (None, "", [])}


def sync_subnet(kea, cfg, mapping, dry_run):
    family = int(mapping.get("family", 4))
    service = "dhcp4" if family == 4 else "dhcp6"
    subnet_id = int(mapping["kea_subnet_id"])
    wanted = desired_reservations(cfg, mapping)
    current = {normalise_mac(h.get("hw-address")): h
               for h in kea.reservations(service, subnet_id) if h.get("hw-address")}
    added = removed = 0

    for mac, host in list(current.items()):
        if mac in wanted and comparable(host, family) == comparable(wanted[mac], family):
            continue
        if mac not in wanted and not cfg.get("delete_missing", True):
            continue
        LOG.info("%s subnet %s: removing reservation %s", service, subnet_id, mac)
        if not dry_run:
            answer = kea.command(service, "reservation-del", {
                "subnet-id": subnet_id, "identifier-type": "hw-address", "identifier": mac})
            if answer.get("result") not in (0, 3):
                LOG.error("reservation-del %s failed: %s", mac, answer.get("text"))
                continue
        removed += 1
        current[mac] = None

    for mac, res in wanted.items():
        if current.get(mac) is not None:
            continue
        LOG.info("%s subnet %s: adding reservation %s -> %s", service, subnet_id, mac,
                 res.get("ip-address") or res.get("ip-addresses"))
        if not dry_run:
            answer = kea.command(service, "reservation-add", {"reservation": res})
            if answer.get("result") != 0:
                LOG.error("reservation-add %s failed: %s", mac, answer.get("text"))
                continue
        added += 1
    return added, removed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-c", "--config", default="/etc/kea/kea-phpipam-sync.json")
    parser.add_argument("-n", "--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    kea = Kea(cfg)
    failed = False
    for mapping in cfg["subnet_map"]:
        try:
            added, removed = sync_subnet(kea, cfg, mapping, args.dry_run)
            LOG.info("phpIPAM subnet %s -> Kea subnet %s: %d added, %d removed",
                     mapping["phpipam_subnet_id"], mapping["kea_subnet_id"], added, removed)
        except (RuntimeError, OSError, ValueError) as err:
            LOG.error("phpIPAM subnet %s: %s", mapping.get("phpipam_subnet_id"), err)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
