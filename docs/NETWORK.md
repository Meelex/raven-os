# Network Setup

LAN configuration, WireGuard VPN, SSH keys, and firewall notes.

---

## LAN

| Node | IP | MAC reservation |
|------|----|----------------|
| Duat | 192.168.1.5 | Set static in router DHCP |
| Raven | 192.168.1.3 | Set static in router DHCP |
| Scarab | 192.168.1.2 | Set static in router DHCP |
| Griffin | 192.168.1.8 | Set static in router DHCP |
| Legiom | 192.168.1.6 | Set static in router DHCP |

Assign static IPs via your router's DHCP reservation table using each device's MAC address. Do not hardcode IPs in `/etc/network/interfaces` — let the router manage it so you can change it from one place.

Subnet: `192.168.1.0/24`
Default gateway: `192.168.1.1` (your router)

---

## WireGuard VPN

Required if any node will travel outside the home network (Scarab, phones).

### Why a VPS relay?

If your ISP uses CGNAT (T-Mobile home internet does), inbound connections to Duat are blocked — Duat has no public IP. The solution: rent a cheap VPS, run WireGuard on it as a relay. All clients connect to the VPS, which routes traffic to Duat over a persistent tunnel.

### VPS Requirements

- Any Linux VPS with a public IP (Hetzner, DigitalOcean, Linode, etc.)
- 1 vCPU / 512MB RAM is enough
- Recommended location: same region as your home for lower latency

### VPS Setup

```bash
# On the VPS
sudo apt update && sudo apt install wireguard

# Generate VPS keypair
wg genkey | tee /etc/wireguard/privatekey | wg pubkey > /etc/wireguard/publickey
cat /etc/wireguard/publickey   # save this — you'll need it in client configs

# /etc/wireguard/wg0.conf on VPS:
[Interface]
Address = 172.16.0.1/24
ListenPort = 443
PrivateKey = <VPS_PRIVATE_KEY>
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

[Peer]
# Duat (Pi 5)
PublicKey = <DUAT_PUBLIC_KEY>
AllowedIPs = 172.16.0.10/32, 192.168.1.0/24

# Add a [Peer] block for each client device

sudo systemctl enable --now wg-quick@wg0
```

Port 443 is used instead of the default 51820 to avoid ISP blocking.

### Client Tunnel IPs

| Device | Tunnel IP |
|--------|-----------|
| Duat | 172.16.0.10 |
| Scarab | 172.16.0.2 |
| fiancée's iPhone | 172.16.0.3 |
| your Pixel | 172.16.0.5 |
| Legiom | 172.16.0.6 |

### Client Config Template

```ini
[Interface]
Address = 172.16.0.X/32
PrivateKey = <CLIENT_PRIVATE_KEY>
DNS = 192.168.1.5

[Peer]
PublicKey = <VPS_PUBLIC_KEY>
Endpoint = <VPS_IP>:443
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
```

`AllowedIPs = 0.0.0.0/0` routes all traffic through the tunnel (full tunnel mode). Use `192.168.1.0/24, 172.16.0.0/24` instead if you only want LAN access with local internet.

### Generate Keys for a New Client

```bash
wg genkey | tee client_private.key | wg pubkey > client_public.key
```

### iPhone Note

Scarab (Pi Zero 2 W) only supports 2.4 GHz WiFi. When using an iPhone as a hotspot, enable "Maximize Compatibility" in Settings → Personal Hotspot to force 2.4 GHz.

### DDNS

If your VPS IP could change, or for a nicer endpoint URL, set up DuckDNS (free):
1. Create an account at duckdns.org
2. Claim a subdomain
3. Run the DuckDNS update script as a cron job on the VPS

---

## SSH Architecture

### Key Setup — Raven → Legiom

```bash
# On Raven
ssh-keygen -t ed25519 -C "raven-os-remote-access" -f ~/.ssh/pc_access_key
# Copy public key content
cat ~/.ssh/pc_access_key.pub
```

Add that public key to Legiom's admin authorized_keys file:
`C:\ProgramData\ssh\administrators_authorized_keys`

### Key Setup — Duat → Legiom

```bash
# On Duat
ssh-keygen -t ed25519 -C "duat@duat" -f ~/.ssh/duat_horus_key
cat ~/.ssh/duat_horus_key.pub
```

Same destination file on Legiom.

### Legiom — Windows OpenSSH (Critical Notes)

The `YourUser` account is a Windows Administrator. OpenSSH on Windows **ignores** per-user `authorized_keys` for admin accounts. Use the system file instead:

```
C:\ProgramData\ssh\administrators_authorized_keys
```

**Permissions** (OpenSSH enforces this strictly — wrong perms = auth rejected):
```powershell
# Set correct permissions (run as Administrator)
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /inheritance:r
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /grant "NT AUTHORITY\SYSTEM:F"
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /grant "BUILTIN\Administrators:F"
```

**sshd_config** (`C:\ProgramData\ssh\sshd_config`) — verify this line is present and uncommented:
```
AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
```

**Firewall scope:** The OpenSSH firewall rule created by Windows defaults to Private network profile only. If your home WiFi is set to Public, SSH will be silently blocked. Fix once:
```powershell
Set-NetConnectionProfile -Name "YourWiFiName" -NetworkCategory Private
```

Run in an elevated PowerShell session.

### Test SSH Connectivity

```bash
# From Raven — test scan ability
ssh -i ~/.ssh/pc_access_key YourUser@192.168.1.6 "dir C:\Users\YourUser\Downloads"

# From Duat — test lock ability
ssh -i ~/.ssh/duat_horus_key YourUser@192.168.1.6 "icacls C:\Users\YourUser\Downloads\test.txt /deny YourUser:(RX)"
```

---

## Pi-hole (Future)

Duat is planned as a Pi-hole DNS server for the LAN (192.168.1.5 as DNS). Not yet deployed. When active, set `DNS = 192.168.1.5` on all LAN devices and WireGuard client configs.

---

## Diagnostics

```bash
# Check WireGuard status
sudo wg show

# Check which peers have recent handshakes (within 3 min = alive)
sudo wg show wg0 latest-handshakes

# Restart WireGuard
sudo wg-quick down wg0 && sudo wg-quick up wg0

# Test Duat services are reachable from LAN
curl http://192.168.1.5:6174/health    # Hash DB
curl http://192.168.1.5:6176/health    # Unlock service
curl http://192.168.1.5:7744/status    # Ring service
curl http://192.168.1.5:5000           # Game server

# Raven heartbeat check — look for UDP packets from Legiom
sudo tcpdump -i wlan0 udp port 7743
```
