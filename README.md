# ansible-role-kea_dhcp

Ansible role that installs and configures the [ISC Kea](https://www.isc.org/kea/) DHCP server on RHEL 8, 9 and 10 (and compatible: AlmaLinux, Rocky, Oracle Linux).

Features:

- DHCPv4 and DHCPv6 with subnets, shared networks, pools, prefix delegation and host reservations (global or per subnet).
- High availability clusters (hot-standby, load-balancing, passive-backup) using the HA hook, with a dedicated HA listener.
- A REST API through the Kea Control Agent with HTTP basic auth and optional TLS. Reservations can be added and removed at run time with `host_cmds`, and an optional phpIPAM sync service is included.
- Performance tuning: multi-threading sized for your lease backend, lease caching, reservation lookup tuning, kernel socket buffers and systemd limits.
- PXE / network boot for BIOS, UEFI (x86, x64, ARM64), UEFI HTTP boot, iPXE chainloading and DHCPv6 boot URLs.
- Dynamic DNS via `kea-dhcp-ddns`, aimed at Microsoft DNS. Secure-only zones work through GSS-TSIG (Kerberos).
- Logging options: file, syslog or stdout, with severity, rotation, extra loggers and forensic lease logging.
- Molecule tests: a single server on EL8, EL9 and EL10, plus a two-node HA cluster with a failover test.

## Requirements

- Ansible core 2.15 or newer.
- Collections from `requirements.yml`: `ansible.posix` and `community.general`.
- Managed hosts need access to the package repositories: ISC's Cloudsmith repository by default (or a local mirror), plus EPEL, which provides `log4cplus` for the ISC packages. The role enables EPEL unless `kea_epel_manage: false`.

```bash
ansible-galaxy collection install -r requirements.yml
```

## Package source

| `kea_install_source` | Repository | Notes |
|---|---|---|
| `isc` (default) | ISC Cloudsmith, `kea_isc_repo_series: "3-0"` | Kea 3.x. From 3.0, every hook (HA, host_cmds, GSS-TSIG, legal log) is open source and ships in `isc-kea-hooks`. |
| `distro` | EPEL (EL8/9), AppStream (EL10) | An older Kea without `host_cmds` or GSS-TSIG. Set `kea_hook_host_cmds: false`. |

If you mirror the repository (Satellite, Pulp, Artifactory), set `kea_manage_repo: false`.

## Quick start

```yaml
- hosts: dhcp_servers
  become: true
  roles:
    - role: ansible-role-kea_dhcp
      vars:
        kea_dhcp4_interfaces: [ens192]
        kea_dhcp4_option_data:
          - { name: domain-name-servers, data: "10.0.0.53, 10.0.0.54" }
          - { name: domain-name, data: corp.example.com }
        kea_dhcp4_subnets:
          - id: 1
            subnet: 10.0.10.0/24
            pools: [{ pool: 10.0.10.100 - 10.0.10.250 }]
            option-data: [{ name: routers, data: 10.0.10.1 }]
            reservations:
              - { hw-address: "aa:bb:cc:dd:ee:01", ip-address: 10.0.10.10, hostname: printer01 }
```

Subnets, shared networks, reservations, options and client classes all use Kea's own JSON schema, written as YAML. Anything in the [Kea ARM](https://kea.readthedocs.io/) can be expressed there. For global settings the role does not model, use `kea_dhcp4_extra_config` (and the `dhcp6`, `ddns` and `ctrl_agent` equivalents), which are merged recursively into the generated configuration.

Every subnet needs a stable, unique `id`. The role checks this, because reservations in a database, HA and lease files all refer to the subnet by its id.

Before installing a configuration file, the role validates it with `kea-<daemon> -t`.

## DHCPv6

```yaml
kea_dhcp6_enabled: true
kea_dhcp6_interfaces: [ens192]
kea_dhcp6_subnets:
  - id: 1
    subnet: 2001:db8:10::/64
    interface: ens192
    pools: [{ pool: 2001:db8:10::1000 - 2001:db8:10::ffff }]
    pd-pools:
      - { prefix: "2001:db8:80::", prefix-len: 48, delegated-len: 56 }
    reservations:
      - duid: "00:03:00:01:aa:bb:cc:dd:ee:02"
        ip-addresses: ["2001:db8:10::10"]
      - hw-address: "aa:bb:cc:dd:ee:03"
        prefixes: ["2001:db8:80:100::/56"]
# Pin the server DUID so it survives reinstalls (and is stable in HA pairs).
kea_dhcp6_server_id: { type: LLT, htype: 1, identifier: "aabbccddeeff", time: 0 }
```

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

- By default each DHCP daemon runs a dedicated HTTP listener for HA traffic: DHCPv4 on `kea_ha_dhcp4_port` (8001) and DHCPv6 on `kea_ha_dhcp6_port` (8002). The role opens these ports in firewalld and labels them for SELinux.
- If you set `kea_ha_dedicated_listener: false`, peers talk through the Control Agent instead. `kea_ctrl_agent_host` must then be an address the peers can reach.
- In `load-balancing` mode, split each pool between the servers with the `HA_<server-name>` classes, for example `client-class: HA_kea1`. See the Kea ARM, "Load-Balancing Configuration".
- To authenticate HA requests, set `kea_ha_basic_auth_user` and `kea_ha_basic_auth_password` (use vault).
- Timers (`kea_ha_heartbeat_delay`, `kea_ha_max_response_delay`, `kea_ha_max_unacked_clients`, ...) map one to one to the HA hook parameters. Use `kea_ha_extra_parameters` for anything else.

When reservations live in a shared hosts database, every node sees changes made through the API at once. When reservations come from the inventory, the role writes the same configuration to every node.

## API and run-time reservations (phpIPAM and others)

The Control Agent listens on `kea_ctrl_agent_host:kea_ctrl_agent_port` (default `127.0.0.1:8000`). By default the role loads the `lease_cmds`, `host_cmds` and `stat_cmds` hooks.

```yaml
kea_ctrl_agent_host: 10.0.0.11
kea_api_users:
  - { user: admin, password: "{{ vault_kea_admin_password }}" }
  - { user: phpipam, password: "{{ vault_kea_phpipam_password }}" }
kea_ctrl_agent_tls_enabled: true          # optional
kea_ctrl_agent_tls_cert_content: "{{ vault_kea_cert }}"
kea_ctrl_agent_tls_key_content: "{{ vault_kea_key }}"
kea_ctrl_agent_tls_ca_content: "{{ vault_ca }}"

# Reservations added through the API are stored in a hosts database.
kea_hosts_database:
  type: mysql                # or postgresql
  name: kea
  host: db.example.com
  user: kea
  password: "{{ vault_kea_db_password }}"
kea_db_init: true            # run kea-admin db-init / db-upgrade once
```

Passwords are written to files in `/etc/kea` (mode 0640) and referenced with `password-file`, so they never appear in the Kea configuration.

`reservation-add` and `reservation-del` need a hosts database. Reservations written in the configuration file are read-only through the API.

Example call from any application:

```bash
curl -su phpipam:secret -H 'Content-Type: application/json' http://10.0.0.11:8000/ -d '{
  "command": "reservation-add", "service": ["dhcp4"],
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
   kea_krb5_manage: true
   kea_krb5_realm: CORP.EXAMPLE.COM
   ```

Classic TSIG keys (`kea_ddns_tsig_keys`) are supported for BIND-style servers. Windows DNS does not support them.

## Logging

| Variable | Default |
|---|---|
| `kea_log_output` | `file`: `/var/log/kea/kea-dhcp4.log`, `kea-dhcp6.log`, `kea-dhcp-ddns.log`, `kea-ctrl-agent.log`. Also `syslog`, `stdout` or `stderr`. |
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
| `kea_ddns_enabled` | `false` |
| `kea_ctrl_agent_enabled` | `true` |
| `kea_ha_enabled` | `false` |
| `kea_pxe_enabled` | `false` |
| `kea_phpipam_sync_enabled` | `false` |
| `kea_firewalld_manage` / `kea_selinux_manage` | `true` / `true` |
| `kea_validate_config` | `true` |

Tags: `kea_install`, `kea_tuning`, `kea_database`, `kea_config`, `kea_firewall`, `kea_service`, `kea_phpipam`.

## Testing

```bash
pip install ansible-core molecule "molecule-plugins[docker]" ansible-lint yamllint
yamllint . && ansible-lint
molecule test             # DHCPv4/v6, DDNS, PXE, API, reservations, logging on EL8/9/10
molecule test -s ha       # two-node hot-standby cluster, failover and recovery
```

The scenarios use the `almalinux/<n>-init` images with systemd and need internet access to the ISC repository. GitHub Actions runs lint and both scenarios on every pull request.

## License

MIT
