# ansible-role-kea_dhcp

Ansible role that installs and configures the [ISC Kea](https://www.isc.org/kea/) DHCP server on RHEL 8, 9 and 10 (and compatible: AlmaLinux, Rocky, Oracle Linux).

Features:

- DHCPv4 and DHCPv6 with subnets, shared networks, pools, prefix delegation and host reservations (global or per subnet). Lease times can be set globally or per subnet.
- DHCPv4 address conflict detection: the ping check hook pings an address before offering it.
- High availability clusters (hot-standby, load-balancing, passive-backup) using the HA hook, with a dedicated HA listener, HTTPS with client certificates between peers, and firewall rules that admit only the peers.
- A REST API with HTTP basic auth and optional TLS, served by the Kea daemons themselves on Kea 2.7.2+ or by the Control Agent on older Kea. Reservations can be added and removed at run time with `host_cmds`, and an optional phpIPAM sync service is included.
- Performance tuning: multi-threading sized for your lease backend, lease caching, reservation lookup tuning, kernel socket buffers and systemd limits.
- PXE / network boot for BIOS, UEFI (x86, x64, ARM64), UEFI HTTP boot, iPXE chainloading and DHCPv6 boot URLs.
- Dynamic DNS via `kea-dhcp-ddns`, aimed at Microsoft DNS. Secure-only zones work through GSS-TSIG (Kerberos).
- Logging options: file, syslog or stdout, with severity, rotation, extra loggers and forensic lease logging.
- SELinux port and file labeling, a fapolicyd trust file for non-RPM files, and FIPS checks (TSIG algorithms, crypto policy for AD Kerberos).
- Configuration changes are applied with a reload, so Kea keeps its leases in memory and HA keeps its state. Only package, systemd and certificate changes restart Kea.
- Molecule tests with podman. They cover:
  - a single server on EL8, EL9 and EL10, with Kea from ISC or AppStream
  - two-node HA clusters in every mode, with failover
  - the move from kea-ctrl-agent to the daemons' API
  - MariaDB databases, the API over HTTPS, and the phpIPAM sync

## Requirements

- Ansible core 2.15 or newer. RHEL 8 targets need ansible-core 2.16 or older on the controller, because 2.17+ no longer supports EL8's Python 3.6, which is where the dnf bindings live. Both 2.15 and 2.16 are end-of-life upstream, so keep a separate, pinned controller environment for EL8 hosts and use a supported release for the rest.
- Collections from `requirements.yml`: `ansible.posix` and `community.general`.
- Managed hosts need access to the package repositories: ISC's Cloudsmith repository by default (or a local mirror), plus EPEL, which provides `log4cplus` for the ISC packages. The role enables EPEL unless `kea_epel_manage: false`. With `kea_install_source: distro` on EL10, everything comes from AppStream and EPEL is not needed.

```bash
ansible-galaxy collection install -r requirements.yml
```

## Package source

| `kea_install_source` | Repository | Notes |
|---|---|---|
| `isc` (default) | ISC Cloudsmith, `kea_isc_repo_series: "3-0"` | Kea 3.x. From 3.0, every hook (HA, host_cmds, GSS-TSIG, legal log) is open source and ships in `isc-kea-hooks`. |
| `distro` | EPEL (EL8/9), AppStream (EL10) | EL10: AppStream's Kea 3.0, with every hook in `kea-hooks`. EL8/EL9: EPEL's Kea 1.8 / 2.6, without `host_cmds`, GSS-TSIG or ping check, so set `kea_hook_host_cmds: false`, and the API stays on kea-ctrl-agent. |

If you mirror the repository (Satellite, Pulp, Artifactory), set `kea_manage_repo: false`.

## Quick start

```yaml
- hosts: dhcp_servers
  become: true
  roles:
    - role: ansible-role-kea_dhcp
      vars:
        kea_dhcp4_interfaces: [ens192]
        # Global options, sent to every subnet unless the subnet sets its own.
        kea_dhcp4_option_data:
          - { name: domain-name-servers, data: "10.0.0.53, 10.0.0.54" }
          - { name: domain-name, data: corp.example.com }
          - { name: domain-search, data: "corp.example.com, example.com" }
        kea_dhcp4_subnets:
          - id: 1
            subnet: 10.0.10.0/24
            pools: [{ pool: 10.0.10.100 - 10.0.10.250 }]
            option-data:
              - { name: routers, data: 10.0.10.1 }     # default gateway
            reservations:
              - { hw-address: "aa:bb:cc:dd:ee:01", ip-address: 10.0.10.10, hostname: printer01 }
          - id: 2
            subnet: 10.0.20.0/24                      # a site with its own DNS and suffix
            pools: [{ pool: 10.0.20.100 - 10.0.20.250 }]
            option-data:
              - { name: routers, data: 10.0.20.1 }
              - { name: domain-name-servers, data: "10.0.20.53, 10.0.0.53" }
              - { name: domain-name, data: site2.corp.example.com }
              - { name: domain-search, data: "site2.corp.example.com, corp.example.com" }
```

| Setting | DHCPv4 option | DHCPv6 option |
|---|---|---|
| Default gateway | `routers` (3), comma-separated, preferred first | none, see [DHCPv6](#dhcpv6) |
| DNS servers | `domain-name-servers` (6) | `dns-servers` (23) |
| Domain name | `domain-name` (15), a single name | none, use `domain-search` |
| Search list | `domain-search` (119), comma-separated | `domain-search` (24) |

Options set in a subnet replace the global option of the same name; they are not merged. Kea checks the most specific level first: reservation, then pool, subnet, shared network, client class and global. Some clients, older Windows releases among them, ignore `domain-search` (option 119), so set `domain-name` as well.

Subnets, shared networks, reservations, options and client classes all use Kea's own JSON schema, written as YAML. Anything in the [Kea ARM](https://kea.readthedocs.io/) can be expressed there. For global settings the role does not model, use `kea_dhcp4_extra_config` (and the `dhcp6`, `ddns` and `ctrl_agent` equivalents), which are merged recursively into the generated configuration.

Set `kea_dhcp4_interfaces` / `kea_dhcp6_interfaces` for every family that has subnets. The role does not default to `"*"`, because Kea would then answer on every interface, management networks included. Relayed-only servers still list the interface the relayed traffic arrives on.

Every subnet needs a stable, unique `id`. The role checks this, because reservations in a database, HA and lease files all refer to the subnet by its id.

Before installing a configuration file, the role validates it with `kea-<daemon> -t`. The configuration files can contain database passwords and TSIG secrets, so those tasks never print a diff, even under `--diff`.

Before it configures, labels or opens any port, the role runs `ss` to check that no other service already listens on one Kea needs. That covers the API ports, the HA listeners, UDP 67/547 and the DDNS port. If another service holds one, the role fails and names the process. Kea's own listeners don't count, so re-runs and the move from kea-ctrl-agent to the daemon sockets go through. It also fails if two of the role's port variables are set to the same port. Set `kea_port_check: false` to skip the check.

## Lease times

The `kea_dhcp4_*` and `kea_dhcp6_*` lifetime variables set the server-wide defaults, in seconds. Any subnet or shared network can set its own with Kea's keys. A subnet inherits from its shared network, and both inherit from the globals, so set only what differs.

| Variable (global) | Default | Per-subnet key |
|---|---|---|
| `kea_dhcp{4,6}_valid_lifetime` | `4000` | `valid-lifetime` |
| `kea_dhcp{4,6}_renew_timer` / `_rebind_timer` | `1000` / `2000` | `renew-timer` / `rebind-timer` |
| `kea_dhcp6_preferred_lifetime` | `3000` | `preferred-lifetime` |
| `kea_dhcp{4,6}_min_valid_lifetime` / `_max_valid_lifetime` | unset | `min-valid-lifetime` / `max-valid-lifetime` |
| `kea_dhcp6_min_preferred_lifetime` / `_max_preferred_lifetime` | unset | `min-preferred-lifetime` / `max-preferred-lifetime` |
| `kea_dhcp4_offer_lifetime` | unset (0) | `offer-lifetime` |
| `kea_calculate_tee_times` | `false` | `calculate-tee-times` |

The min/max bounds limit what a client may ask for. Without them Kea always hands out `valid-lifetime`.

```yaml
kea_dhcp4_subnets:
  - id: 1                      # office: a day
    subnet: 10.0.10.0/24
    pools: [{ pool: 10.0.10.100 - 10.0.10.250 }]
    valid-lifetime: 86400
    renew-timer: 43200
    rebind-timer: 75600
  - id: 2                      # guest Wi-Fi: an hour, clients may ask for 30 min to 2 h
    subnet: 10.0.20.0/24
    pools: [{ pool: 10.0.20.10 - 10.0.20.250 }]
    valid-lifetime: 3600
    min-valid-lifetime: 1800
    max-valid-lifetime: 7200
    calculate-tee-times: true  # T1/T2 at 50% / 87.5% of the lifetime granted

kea_dhcp6_subnets:
  - id: 1
    subnet: "2001:db8:10::/64"
    pools: [{ pool: "2001:db8:10::1000 - 2001:db8:10::1fff" }]
    valid-lifetime: 86400
    preferred-lifetime: 43200
    renew-timer: 21600
    rebind-timer: 34560
```

Kea refuses a configuration where `renew-timer` exceeds `rebind-timer`, or either exceeds `valid-lifetime`. The role's `kea-<daemon> -t` check catches this before anything is installed. In DHCPv6, `preferred-lifetime` should not exceed `valid-lifetime`.

## Address conflict detection (ping check)

With `kea_ping_check_enabled: true`, `kea-dhcp4` pings an address before offering it. If anything replies, Kea drops the offer, records the address as declined and offers another one. This catches devices that were given a static address inside a pool.

```yaml
kea_ping_check_enabled: true
kea_dhcp4_offer_lifetime: 60     # hold offers, so only the first offer is pinged
# kea_ping_check_min_ping_requests: 1
# kea_ping_check_reply_timeout: 100   # ms per echo request
kea_dhcp4_subnets:
  - id: 3
    subnet: 10.0.30.0/24       # a slow WAN segment: wait longer
    user-context: { min-ping-requests: 2, reply-timeout: 250 }
  - id: 4
    subnet: 10.0.40.0/24       # high-churn segment: skip the check
    user-context: { enable-ping-check: false }
```

- DHCPv4 only. For DHCPv6, clients run duplicate address detection themselves and send a DECLINE, which Kea already handles.
- Hosts that drop ICMP echo (Windows' firewall does by default) are not detected.
- Every offer waits up to `min-ping-requests × reply-timeout`, so ISC does not recommend it for high-throughput servers.
- The hook ships with ISC's packages and with AppStream's Kea 3.0 on EL10, not with EPEL's Kea on EL8/EL9. The role fails early in that case.
- Pinging needs `CAP_NET_RAW`. The packaged units already grant it, and the role's systemd drop-in adds it again for `kea-dhcp4`.

## Authoritative mode

`kea_dhcp4_authoritative` decides what `kea-dhcp4` does when a client asks to keep an address Kea has no lease for. This happens when a laptop comes back from sleep, moves from another network, or Kea has lost its lease file.

- `false` (the default, as in Kea): Kea ignores the request. The client waits for it to time out, often 10 seconds or more, before starting over with DHCPDISCOVER, and some clients keep using the stale address meanwhile.
- `true`: Kea answers with DHCPNAK, and the client starts over at once.

Turn it on where Kea is the only DHCP server for a segment, which includes an HA pair. Leave it off while another server can still answer on the same network, for example during a migration from Windows DHCP with both servers running. In that case Kea would NAK clients the other server leased to.

A subnet or shared network can override the global value:

```yaml
kea_dhcp4_authoritative: false
kea_dhcp4_subnets:
  - id: 1
    subnet: 10.0.10.0/24
    authoritative: true        # Kea alone serves this segment
```

This setting is DHCPv4 only. For a DHCPv6 client on the wrong link, Kea already answers with a NotOnLink status.

## NTP servers

NTP servers are handed out as ordinary options, either globally or per subnet or shared network. A subnet's list replaces the global one.

| Family | Option | Kea name | Value |
|---|---|---|---|
| DHCPv4 | 42 | `ntp-servers` | comma-separated IPv4 addresses |
| DHCPv6 | 31 | `sntp-servers` | comma-separated IPv6 addresses |
| DHCPv6 | 56 | `ntp-server` | a container that holds one suboption: `ntp-server-address`, `ntp-server-multicast` or `ntp-server-fqdn` |

```yaml
kea_dhcp4_option_data:
  - { name: ntp-servers, data: "10.0.0.123, 10.0.0.124" }
kea_dhcp4_subnets:
  - id: 2
    subnet: 10.0.20.0/24       # a branch office with its own time source
    option-data:
      - { name: routers, data: 10.0.20.1 }
      - { name: ntp-servers, data: 10.0.20.1 }

kea_dhcp6_option_data:
  - { name: sntp-servers, data: "2001:db8::123, 2001:db8::124" }
  # Option 56 (RFC 5908). List the container, then its suboption.
  - { name: ntp-server }
  - { name: ntp-server-address, space: v6-ntp-server-suboptions, data: "2001:db8::123" }
```

- Use option 42, not `time-servers` (option 4). Option 4 is the RFC 868 Time protocol, which NTP clients do not use.
- RFC 5908 permits one suboption per option 56 instance, so the role's option-data can publish only one address or FQDN that way. For more than one server, use `sntp-servers`, which most DHCPv6 clients request anyway (NetworkManager, systemd-networkd, dhcpcd).
- Kea sends these options only when the client asks for them. To send them regardless, add `always-send: true` to the entry.
- Clients have to use what they receive. On RHEL, NetworkManager passes the DHCP NTP servers to chronyd through `/run/chrony-dhcp` (the `sourcedir` line in `chrony.conf`). Windows ignores option 42 by default, and domain members take their time from the domain hierarchy.

## DHCPv6

```yaml
kea_dhcp6_enabled: true
kea_dhcp6_interfaces: [ens192]
kea_dhcp6_option_data:
  - { name: dns-servers, data: "2001:db8::53, 2001:db8::54" }
  - { name: domain-search, data: "corp.example.com, example.com" }
kea_dhcp6_subnets:
  - id: 1
    subnet: 2001:db8:10::/64
    interface: ens192
    pools: [{ pool: 2001:db8:10::1000 - 2001:db8:10::ffff }]
    pd-pools:
      - { prefix: "2001:db8:80::", prefix-len: 48, delegated-len: 56 }
    option-data:                 # replaces the global lists for this subnet
      - { name: dns-servers, data: "2001:db8:10::53, 2001:db8::53" }
      - { name: domain-search, data: "site1.corp.example.com, corp.example.com" }
    reservations:
      - duid: "00:03:00:01:aa:bb:cc:dd:ee:02"
        ip-addresses: ["2001:db8:10::10"]
      - hw-address: "aa:bb:cc:dd:ee:03"
        prefixes: ["2001:db8:80:100::/56"]
# Pin the server DUID so it survives reinstalls (and is stable in HA pairs).
kea_dhcp6_server_id: { type: LLT, htype: 1, identifier: "aabbccddeeff", time: 0 }
```

DHCPv6 cannot hand out a default gateway. Hosts learn their default route from the router advertisements (RAs) on the link. For clients to ask Kea for addresses at all, the router's RAs need the M (managed) flag, and the O (other configuration) flag if clients should fetch only DNS and other options. There is no domain-name option either. The `domain-search` list takes its place.

## High availability

Put the same variables on every node, for example in `group_vars/dhcp_servers.yml`. Each node finds itself in `kea_ha_peers` by `kea_ha_this_server_name`, which defaults to `inventory_hostname`.

```yaml
kea_ha_enabled: true
kea_ha_mode: hot-standby          # or load-balancing / passive-backup
kea_ha_peers:
  - { name: kea1, address: 10.0.0.11, role: primary }
  - { name: kea2, address: 10.0.0.12, role: standby }   # "secondary" for load-balancing
  # - { name: kea3, address: 10.0.0.13, role: backup }
kea_dhcp6_enabled: true           # both families get their own HA relationship
```

- By default each DHCP daemon runs a dedicated HTTP listener for HA traffic: DHCPv4 on `kea_ha_dhcp4_port` (8001) and DHCPv6 on `kea_ha_dhcp6_port` (8002). firewalld accepts those ports only from the addresses in `kea_ha_peers`, through rich rules. Set `kea_firewalld_ha_restrict_to_peers: false` to open them to the whole zone.
- If you set `kea_ha_dedicated_listener: false`, peers talk through kea-ctrl-agent instead, so the role keeps running it even on Kea 3.x. `kea_api_host` must then be an address the peers can reach.
- In `load-balancing` mode, split each pool between the servers with the `HA_<server-name>` classes, for example `client-class: HA_kea1`. See the Kea ARM, "Load-Balancing Configuration".
- Lease updates between peers carry MAC addresses and host names, so turn on HTTPS. Each node needs a certificate signed by a CA all peers trust, with the node's IP address as a subjectAltName and both `serverAuth` and `clientAuth` extended key usage. The node uses it for its own listener and for its connections to the partner. With `kea_ha_tls_require_client_certs` (the default), the listener refuses any peer without such a certificate:
  ```yaml
  kea_ha_tls_enabled: true
  kea_ha_tls_ca_content: "{{ vault_ca }}"
  ssl_certificates:                                     # see "TLS certificates"
    - name: "{{ ansible_facts['fqdn'] }}"               # this node's certificate
      certificate:
        content: "{{ vault_kea_ha_cert }}"
      key:
        content: "{{ vault_kea_ha_key }}"
  ```
  The files default to the API's (`kea_api_tls_*_file`), so one certificate can serve both. Without a dedicated listener, peers connect to kea-ctrl-agent, so `kea_ha_tls_enabled` must then equal `kea_api_tls_enabled`.
- `kea_ha_basic_auth_user` and `kea_ha_basic_auth_password` make the HA client send credentials. Only kea-ctrl-agent checks them, which happens without a dedicated listener. The dedicated listener does not authenticate requests, so protect it with client certificates and the peer-only firewall rules above.
- Timers (`kea_ha_heartbeat_delay`, `kea_ha_max_response_delay`, `kea_ha_max_unacked_clients`, ...) map one to one to the HA hook parameters. Use `kea_ha_extra_parameters` for anything else.

When reservations live in a shared hosts database, every node sees changes made through the API at once. When reservations come from the inventory, the role writes the same configuration to every node.

Run plays against a cluster with `serial: 1`, so one node at a time changes (see [Operations](#operations)). The playbook below runs the HA pair one node at a time and lets the pair settle before moving on:

```yaml
- hosts: dhcp_servers
  become: true
  serial: 1
  roles:
    - ansible-role-kea_dhcp
  post_tasks:
    - name: Wait for HA to settle
      ansible.builtin.uri:
        url: http://127.0.0.1:8004/
        method: POST
        body_format: json
        body: { command: ha-heartbeat }
        url_username: admin
        url_password: "{{ vault_kea_admin_password }}"
        force_basic_auth: true
      register: hb
      until: hb.json[0].arguments.state | default('') in ['hot-standby', 'load-balancing', 'passive-backup']
      retries: 30
      delay: 5
```

## API and run-time reservations (phpIPAM and others)

Since Kea 2.7.2, each daemon can serve the REST API itself, and ISC has deprecated kea-ctrl-agent (it logs a deprecation warning at startup). `kea_api_mode` picks where the API lives:

| `kea_api_mode` | API endpoints | kea-ctrl-agent |
|---|---|---|
| `auto` (default) | On Kea 2.7.2+ (ISC's 3.x packages, AppStream on EL10): one port per daemon. On older Kea (EPEL on EL8/EL9): `:8000`. | Stopped and disabled on 2.7.2+. It keeps running on older Kea, and for HA without a dedicated listener. |
| `daemon` | One port per daemon. The role fails on Kea older than 2.7.2. | Stopped and disabled |
| `ctrl_agent` | `kea_api_host:kea_ctrl_agent_port` (`:8000`) for every service | Running |

The daemons listen on `kea_api_host` (default `127.0.0.1`):

| Daemon | Port variable | Default |
|---|---|---|
| `kea-dhcp4` | `kea_api_dhcp4_port` | `8004` |
| `kea-dhcp6` | `kea_api_dhcp6_port` | `8006` |
| `kea-dhcp-ddns` | `kea_api_ddns_port` | `8053` |

Authentication, TLS and the firewall rules are the same in every mode. A daemon accepts and ignores the `service` parameter, so a client that sends `"service": ["dhcp4"]` only needs its URL changed when moving off kea-ctrl-agent. Each daemon still has its local UNIX control socket too. By default the role loads the `lease_cmds`, `host_cmds` and `stat_cmds` hooks.

```yaml
kea_api_host: 10.0.0.11
kea_api_users:
  - { user: admin, password: "{{ vault_kea_admin_password }}" }
  - { user: phpipam, password: "{{ vault_kea_phpipam_password }}" }
kea_api_tls_enabled: true          # optional: HTTPS
kea_api_tls_ca_content: "{{ vault_ca }}"
ssl_certificates:                  # see "TLS certificates"
  - name: kea1.example.com
    certificate:
      content: |
        -----BEGIN CERTIFICATE-----
        ...
        -----END CERTIFICATE-----
    key:
      content: !vault |
        $ANSIBLE_VAULT;1.1;AES256
        ...

# Reservations added through the API are stored in a hosts database.
kea_hosts_database:
  type: mysql                # or postgresql
  name: kea
  host: db.example.com
  user: kea
  password: "{{ vault_kea_db_password }}"
kea_db_init: true            # run kea-admin db-init / db-upgrade once
```

API passwords are written to files in `/etc/kea` (mode 0640) and referenced with `password-file`, so they never appear in the Kea configuration. Database passwords (`kea_hosts_database.password`, SQL lease databases) and TSIG secrets are written into the configuration files, which are mode 0640 and owned by root and the Kea group.

When `kea_api_host` is not a loopback address, the role requires `kea_api_users`. Anyone who can reach an unauthenticated API can reconfigure or stop Kea. If a firewall or VPN already guards the port, set `kea_api_allow_unauthenticated: true`.

With `kea_api_tls_cert_required: true`, clients must also present a certificate signed by `kea_api_tls_ca_file`.

### TLS certificates

Certificates and keys for the API and HA come from `ssl_certificates`, the list the Apache, phpIPAM and MariaDB roles also read, so one inventory variable can serve every role on a host.

- The API uses `kea_api_tls_certificate` if you set it, then the entry named after the host's FQDN, then the first entry. HA uses `kea_ha_tls_certificate`, which defaults to the API's choice. If HA uses a different entry, also set `kea_ha_tls_cert_file` and `kea_ha_tls_key_file`.
- Only entries in use are written, as `/etc/kea/tls/<name>.crt` (0644) and `<name>.key` (`root:kea` 0640). `kea_api_tls_cert_file`, `kea_api_tls_key_file` and the HA paths link to them.
- Put any intermediate certificates in `certificate.content`, after the server certificate. `ssl_certificates` has no CA field, so CAs stay in `kea_api_tls_ca_content` and `kea_ha_tls_ca_content`.
- Encrypt each key with Ansible Vault (`ansible-vault encrypt_string --stdin-name content < server.key`). Before writing anything, the role checks that each certificate and key are valid PEM, the key has no passphrase, the key belongs to the certificate, and the certificate hasn't expired. If a check fails, the run stops with the existing files and running Kea untouched. Keys stay out of the output, including with `-vvv` and `--diff`.
- If the list is empty, the role writes no certificates and expects something else to put the files at those paths.

`reservation-add` and `reservation-del` need a hosts database. Reservations written in the configuration file are read-only through the API.

Example call from any application:

```bash
curl -su phpipam:secret -H 'Content-Type: application/json' http://10.0.0.11:8004/ -d '{
  "command": "reservation-add",
  "arguments": {"reservation": {"subnet-id": 1, "hw-address": "aa:bb:cc:dd:ee:10",
                                "ip-address": "10.0.10.20", "hostname": "lab-host"}}}'
```

### phpIPAM sync

phpIPAM has no built-in push to Kea, so the role can install a small sync service, `/usr/local/libexec/kea-phpipam-sync`, that runs on a systemd timer. It reads every address that has a MAC in the mapped phpIPAM subnets and reconciles them with Kea reservations through `reservation-get-page`, `reservation-add` and `reservation-del`. Turn it on for one node only. With a shared hosts database, the whole cluster gets the changes.

```yaml
# host_vars/kea1.yml
kea_phpipam_sync_enabled: true
kea_phpipam_url: https://ipam.example.com
kea_phpipam_app_id: kea                    # API app with "App code" security
kea_phpipam_token: "{{ vault_phpipam_token }}"
kea_phpipam_subnet_map:
  - { phpipam_subnet_id: 7, kea_subnet_id: 1, family: 4 }
  - { phpipam_subnet_id: 9, kea_subnet_id: 1, family: 6 }
kea_phpipam_sync_interval: 5min
```

The sync uses the dhcp4 and dhcp6 API URLs that match `kea_api_mode`. Set `kea_phpipam_api_urls` (`{dhcp4: …, dhcp6: …}`) to point it somewhere else.

- **TLS.** With `kea_api_tls_enabled`, the sync checks Kea's certificate against `kea_api_tls_ca_file` (`kea_phpipam_api_ca_file`). When the API requires client certificates, it presents the server's own certificate (`kea_phpipam_api_cert_file` / `_key_file`), which then needs the `clientAuth` extended key usage. phpIPAM's certificate is checked separately (`kea_phpipam_verify_tls`, `kea_phpipam_ca_file`).
- **Deletions.** With `kea_phpipam_delete_missing` (the default), reservations whose address is gone from phpIPAM are removed. Three guards stop a phpIPAM fault from wiping Kea:
  - A subnet that phpIPAM does not know is an error, not an empty list.
  - If phpIPAM returns no addresses at all for a subnet, the sync refuses to remove every reservation in it, unless `kea_phpipam_allow_empty_source: true`.
  - `kea_phpipam_max_deletes` caps removals per subnet and run.

  A refused subnet is logged, makes the run fail (visible with `systemctl status kea-phpipam-sync`), and leaves Kea unchanged.

Run it by hand with `kea-phpipam-sync --dry-run -v`.

## Performance tuning

| Variable | Default | Purpose |
|---|---|---|
| `kea_multi_threading_enabled` | `true` | Packet processing on a thread pool. |
| `kea_dhcp{4,6}_thread_pool_size` / `_packet_queue_size` | `null` | `null` uses ISC's recommended sizes for the lease backend (memfile 4/28 v4, MySQL 12/792, PostgreSQL 8/88 ...). |
| `kea_cache_threshold` | `0.25` | Reuse a lease on renew without a backend write. |
| `kea_dhcp{4,6}_host_reservation_identifiers` | `[hw-address, client-id]` / `[hw-address, duid]` | Fewer identifiers mean fewer lookups. |
| `kea_reservations_global` / `_in_subnet` / `_out_of_pool` | `false` / `true` / `false` | Turn off lookups you don't need. `out_of_pool: true` is faster when every reservation is outside the pools. |
| `kea_dhcp4_socket_type` | `raw` | `udp` is faster when every client is relayed. |
| `kea_dhcp4_lease_database.lfc-interval` | `3600` | Lease file cleanup interval for memfile. |
| `kea_sysctl_settings` | larger `rmem`/`wmem`, `netdev_max_backlog` | Absorbs bursts after an outage. Written to `/etc/sysctl.d/90-kea.conf`. |
| `kea_systemd_limit_nofile` | `65536` | Applied with a systemd drop-in, along with restart on failure. |

## PXE boot

```yaml
kea_pxe_enabled: true
kea_pxe_next_server: 10.0.0.20              # TFTP server
kea_pxe_v4_architectures:                   # defaults shown in defaults/main.yml
  - { name: PXE_BIOS, arch: "0x0000", boot_file: pxelinux.0 }
  - { name: PXE_UEFI_X64, arch: "0x0007", boot_file: shimx64.efi }
  - { name: PXE_UEFI_BC, arch: "0x0009", boot_file: shimx64.efi }
  - { name: PXE_UEFI_ARM64, arch: "0x000b", boot_file: shimaa64.efi }
kea_pxe_http_boot_url_x64: http://10.0.0.20/EFI/BOOT/BOOTX64.EFI   # UEFI HTTP boot
kea_pxe_ipxe_script_url: http://10.0.0.20/boot.ipxe                # iPXE chainload
kea_pxe_v6_architectures:
  - { name: PXE6_UEFI_X64, arch: "0x0007", url: "tftp://[2001:db8::20]/shimx64.efi" }
```

Each architecture becomes a client class matched on option 93 (DHCPv4) or option 61 (DHCPv6). When an iPXE script URL is set, clients that identify as iPXE (user class, option 77) get the script, and the firmware classes skip them. HTTP boot clients also get `vendor-class-identifier: HTTPClient`, which UEFI requires. To restrict network boot to some subnets, set `kea_pxe_enabled: false` and add classes yourself in `kea_dhcp4_client_classes`, using `only-in-additional-list` / `evaluate-additional-classes` on the subnet.

## Dynamic DNS with Microsoft DNS

Windows clients send their name in the FQDN option (81 for v4, 39 for v6). Kea then asks `kea-dhcp-ddns` to update the A/AAAA and PTR records. The defaults match Windows DHCP's "Always dynamically update DNS records" setting. The server updates both records for the client, so the records belong to the DHCP service account, and Kea removes them when the lease expires.

```yaml
kea_ddns_enabled: true
kea_ddns_qualifying_suffix: corp.example.com.
kea_ddns_dns_servers:
  - { ip-address: 10.0.0.53 }
  - { ip-address: 10.0.0.54 }
kea_ddns_forward_domains: [{ name: corp.example.com. }]
kea_ddns_reverse_domains: [{ name: 10.0.10.in-addr.arpa. }]
# Set this when taking over records created by Windows DHCP or by clients:
# kea_ddns_conflict_resolution_mode: no-check-without-dhcid
```

For DHCPv6 clients (with `kea_dhcp6_enabled: true`), the same forward zone gets AAAA records. Add an `ip6.arpa` zone for the PTRs; its name is the prefix's nibbles in reverse order. The DNS servers can be reached over IPv6 as well:

```yaml
kea_ddns_dns_servers:
  - { ip-address: 10.0.0.53 }
  - { ip-address: "2001:db8:10::53" }
kea_ddns_reverse_domains:
  - { name: 10.0.10.in-addr.arpa. }
  - { name: 0.1.0.0.8.b.d.0.1.0.0.2.ip6.arpa. }   # 2001:db8:10::/48
  # A zone can name its own servers instead of using kea_ddns_dns_servers:
  - name: 0.0.0.0.0.2.0.0.8.b.d.0.1.0.0.2.ip6.arpa.   # 2001:db8:20::/64
    dns-servers:
      - { ip-address: "2001:db8:10::54" }
```

Quote IPv6 addresses in YAML. Unquoted, an all-digit address such as `2001:0:0:0:0:0:0:53` is read as a base-60 number, and one ending in `::` breaks a `{ }` mapping.

**Zones set to "Nonsecure and secure"** work as shown above.

**Zones set to "Secure only"**, the AD-integrated default, need GSS-TSIG:

1. Create an AD service account, for example `kea-ddns`. Give it rights to update the zones, either through the zone ACL or by adding it to `DnsUpdateProxy`.
2. Export a keytab on a domain controller:
   ```powershell
   ktpass -princ kea-ddns@CORP.EXAMPLE.COM -mapuser CORP\kea-ddns -pass * `
          -crypto AES256-SHA1 -ptype KRB5_NT_PRINCIPAL -out kea-ddns.keytab
   ```
3. Configure the role:
   ```yaml
   kea_ddns_gss_tsig_enabled: true
   kea_ddns_gss_tsig_server_principal: DNS/dc1.corp.example.com@CORP.EXAMPLE.COM
   kea_ddns_gss_tsig_client_principal: kea-ddns@CORP.EXAMPLE.COM
   kea_ddns_gss_tsig_keytab_b64: "{{ vault_kea_ddns_keytab_b64 }}"   # base64 of the keytab
   kea_ddns_gss_tsig_servers:
     - { id: dc1, ip-address: 10.0.0.53 }
     - { id: dc2, ip-address: 10.0.0.54 }
     # or over IPv6:
     # - { id: dc1-v6, ip-address: "2001:db8:10::53" }
   kea_krb5_manage: true
   kea_krb5_realm: CORP.EXAMPLE.COM
   ```

Classic TSIG keys (`kea_ddns_tsig_keys`) are supported for BIND-style servers. Windows DNS does not support them.

## Firewall

When firewalld is running (`kea_firewalld_manage`, zone `kea_firewalld_zone`):

- The `dhcp` and `dhcpv6` services are allowed for the enabled families.
- The API ports are opened to the zone when `kea_api_host` is not a loopback address (`kea_firewalld_open_api`).
- HA ports accept only the peers' addresses, through rich rules (`kea_firewalld_ha_restrict_to_peers`).
- With `kea_firewalld_remove_stale` (the default), the role also removes what it no longer needs. Examples are kea-ctrl-agent's port after the API moves to the daemons, an old API port, and the rule for a former HA peer. It leaves a port alone if another service is now listening on it.

## SELinux, fapolicyd and FIPS

**SELinux.** When SELinux is enabled, the role works with it and never changes the mode. Turn this off with `kea_selinux_manage: false`.

- RHEL's targeted policy (8, 9 and 10) has no Kea module. Kea's binaries are labelled `bin_t`, and Kea runs as `unconfined_service_t`, which may bind any port. So by default the role labels no ports, and changes no existing port types. 8000 (`soundd_port_t`) and 8002 (`xen_port_t`), for example, stay as they are.
- If a Kea policy module is loaded, so Kea runs in its own domain, the role labels the API and HA ports with that policy's `kea*_port_t` type. Set `kea_selinux_port_type` to choose the type, or to label ports in any case.
- It runs `restorecon` on `/etc/kea`, `/var/lib/kea` and `/var/log/kea`. If you move those directories with `kea_config_dir`, `kea_lib_dir` or `kea_log_dir`, the role adds `semanage fcontext -e` equivalence rules first, so the new paths get the same labels.
- It labels the GSS-TSIG keytab `krb5_keytab_t`.
- You can set booleans (`kea_selinux_booleans`) and extra file contexts (`kea_selinux_fcontexts`).
- For troubleshooting only, and only with a Kea policy module, `kea_selinux_permissive_domain: true` puts just the Kea domain (`kea_selinux_domain`, default `kea_t`) in permissive mode. Collect denials with `ausearch -m AVC -c kea`, build a local module, then turn it back off. Without a module the role refuses this setting, since SELinux doesn't restrict Kea in the first place.

**fapolicyd.** fapolicyd already trusts everything installed by RPM, which covers Kea, its hooks and Python. When fapolicyd is present, the role writes `/etc/fapolicyd/trust.d/kea-dhcp` with the path, size and SHA-256 of every file it deploys outside RPM, then runs `fapolicyd-cli --update`. That covers the phpIPAM sync script, plus anything you list in `kea_fapolicyd_trusted_files` (for example custom hook libraries). Because the role recomputes the hashes on every run, updated files stay trusted.

**FIPS.** The role does not switch FIPS mode on, because that needs `fips-mode-setup` or a FIPS install, and a reboot. What it does on FIPS hosts:

- `kea_fips_required: true` fails the play when the host is not in FIPS mode.
- In FIPS mode, TSIG keys must use HMAC-SHA256 or stronger. The role rejects HMAC-MD5, which is also what Kea uses when a key has no algorithm.
- Active Directory Kerberos only offers AES-SHA1 encryption types, and the default FIPS policy on RHEL 9 and later rejects them. To use GSS-TSIG against Windows DNS on such a host, set `kea_crypto_policy: "FIPS:AD-SUPPORT"` and reboot. The role warns when this applies.
- Kea, its TLS (API, HA, databases) and the phpIPAM sync all use the system OpenSSL, so they follow the system FIPS policy. Use RSA keys of 2048 bits or more, or ECDSA P-256/P-384, for the API certificates.

## Operations

**Configuration changes.** Configuration, password-file and HA changes reload the daemons. A reload sends SIGHUP through the units' `ExecReload`. Kea re-reads its files, keeps its in-memory leases, and HA keeps its state. Package upgrades, systemd drop-in changes, SELinux port changes and new certificates restart the affected daemons. In a cluster a restart makes HA resync with the partner, so:

- Run plays with `serial: 1`, and wait for `ha-heartbeat` to report the normal state before the next node (see the example under [High availability](#high-availability)).
- With `kea_package_state: latest`, upgrade one node at a time, and read ISC's release notes first. HA peers should run the same Kea version except during the upgrade.

**Health checks.** These commands go to a daemon's API (`:8004` for DHCPv4), or to kea-ctrl-agent with `"service": ["dhcp4"]`:

| Command | Answers |
|---|---|
| `status-get` | uptime, reload time, multi-threading and HA state |
| `ha-heartbeat` | HA state (`hot-standby`, `load-balancing`, `partner-down`, ...) |
| `statistic-get-all` / `stat-lease4-get` | packet counters and per-subnet lease use (`stat_cmds`) |
| `config-get` | the running configuration (Kea masks passwords) |

**Backups.**
- With memfile, leases live in `kea_lib_dir` (`kea-leases4.csv`, `kea-leases6.csv`, plus `.2` files while lease file cleanup runs every `lfc-interval`). Back up that directory, or rely on the HA partner, which holds a full copy.
- Back up SQL lease and hosts databases with the database's own tools.
- The configuration is generated by the role, so the inventory is its backup.

**Troubleshooting.**
- `kea-dhcp4 -t /etc/kea/kea-dhcp4.conf` checks a configuration. The role runs the same check before installing one.
- Logs are in `kea_log_dir`. Raise one area with an extra logger, for example `kea-dhcp4.packets` at DEBUG level 55, rather than the whole daemon.
- If no DHCPv4 offers go out for directly connected clients, check `kea_dhcp4_interfaces` and that `kea_dhcp4_socket_type` is `raw`.
- If HA stays in `waiting` or `communications-interrupted`, test the peer URL from each node: `curl --cacert … --cert … --key … https://<peer>:8001/`. Check the firewall rich rules (`firewall-cmd --list-rich-rules`) and the certificates' IP subjectAltNames.

## Logging

| Variable | Default |
|---|---|
| `kea_log_output` | `file`: `/var/log/kea/kea-dhcp4.log`, `kea-dhcp6.log`, `kea-dhcp-ddns.log`, and `kea-ctrl-agent.log` when it runs. Also `syslog`, `stdout` or `stderr`. |
| `kea_log_severity` / `kea_log_debuglevel` | `INFO` / `0` |
| `kea_log_maxsize` / `kea_log_maxver` | 10 MiB / 8 files. Kea rotates the files itself. |
| `kea_log_pattern` | empty, which uses Kea's default pattern (best for log shippers) |
| `kea_dhcp4_extra_loggers` (and v6, ddns, ctrl_agent) | extra child loggers, e.g. `kea-dhcp4.packets` at DEBUG |
| `kea_dhcp4_loggers` (and v6, ...) | complete override of the loggers list |
| `kea_legal_log_enabled` | forensic lease log in `/var/log/kea/kea-legal{4,6}.*.txt` |

## Variables

All variables, with comments, are in [`defaults/main.yml`](defaults/main.yml). The main switches:

| Variable | Default |
|---|---|
| `kea_dhcp4_enabled` / `kea_dhcp6_enabled` | `true` / `false` |
| `kea_dhcp4_interfaces` / `kea_dhcp6_interfaces` | `[]`, required once a family has subnets |
| `kea_dhcp4_authoritative` | `false` (see [Authoritative mode](#authoritative-mode)) |
| `kea_ddns_enabled` | `false` |
| `kea_api_enabled` | `true` |
| `kea_api_mode` | `auto` (see [API](#api-and-run-time-reservations-phpipam-and-others)) |
| `kea_api_host` / `kea_api_users` | `127.0.0.1` / `[]` (users are required for a non-loopback host) |
| `kea_api_tls_enabled` / `kea_ha_tls_enabled` | `false` / `false` |
| `ssl_certificates` | `[]` (see [TLS certificates](#tls-certificates)) |
| `kea_ha_enabled` | `false` |
| `kea_pxe_enabled` | `false` |
| `kea_ping_check_enabled` | `false` |
| `kea_phpipam_sync_enabled` | `false` |
| `kea_firewalld_manage` / `kea_selinux_manage` / `kea_fapolicyd_manage` | `true` / `true` / `true` |
| `kea_fips_required` | `false` |
| `kea_validate_config` | `true` |
| `kea_port_check` | `true` |

Tags: `kea_assert`, `kea_install`, `kea_ports`, `kea_tuning`, `kea_database`, `kea_config`, `kea_security` (`kea_selinux`, `kea_fips`, `kea_fapolicyd`), `kea_firewall`, `kea_service`, `kea_phpipam`. Later tasks rely on facts that `kea_install` sets, such as the detected Kea version and API mode, so include it when you limit a run with tags.

See [CHANGELOG.md](CHANGELOG.md) for changes that need action when upgrading the role.

## Testing

Tests run locally with podman, on the AlmaLinux 10 WSL distribution (any EL10 host works too). The repository directory must be named `ansible-role-kea_dhcp`.

```bash
scripts/test.sh              # lint + every scenario
scripts/test.sh lint         # yamllint + ansible-lint only
scripts/test.sh default      # DHCPv4/v6, DDNS, PXE, API, reservations, logging, port check on EL9 and EL10
scripts/test.sh ha           # two-node hot-standby cluster: HA over HTTPS, peer-only firewall, failover
scripts/test.sh ha-lb        # the same in load-balancing mode
scripts/test.sh ha-backup    # the same in passive-backup mode (no failover)
scripts/test.sh el8          # the default scenario on EL8 under ansible-core 2.16, API on kea-ctrl-agent
scripts/test.sh migrate      # kea-ctrl-agent to daemon API on a live host: firewall, reload not restart
scripts/test.sh integration  # MariaDB lease/hosts databases with kea_db_init, API over HTTPS with
                             # client certificates, phpIPAM sync and its deletion guards (mock phpIPAM)
scripts/test.sh distro       # the default scenario with Kea from AppStream on EL10
```

The containers can't cover these, so test them on a VM before relying on them:
- SELinux in enforcing mode (it is disabled inside containers)
- fapolicyd enforcement (the tests only check the trust file)
- GSS-TSIG against Active Directory
- PostgreSQL databases
- EPEL's older Kea on EL8 and EL9

The script installs podman, Python and git with dnf if they are missing. It builds two virtualenvs under `.cache/test/`: one with current ansible-core, molecule, molecule-plugins[podman], ansible-lint and yamllint, and one with ansible-core 2.16 for EL8. Each virtualenv gets its own pinned collections.

The scenarios run systemd inside the `docker.io/almalinux/<n>-init` images and need internet access to Docker Hub, the ISC repository and EPEL. Podman runs rootless by default. If rootless systemd containers don't work in your WSL setup (for example cgroup v2 delegation is missing), run `sudo scripts/test.sh` to use rootful podman. WSL needs systemd enabled (`[boot] systemd=true` in `/etc/wsl.conf`) for rootless podman.

## License

MIT
