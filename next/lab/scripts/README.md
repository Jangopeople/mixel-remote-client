# Lab helper scripts

Helpers used to test Mixel Remote on the Proxmox lab VMs (Windows 10 = 133, Windows 11 = 134,
Ubuntu = 130). They were written for a working folder `_scratch/lab` and contain that path;
copy them there (or adjust the paths) before use. No credentials are stored here.

- `upd.sh`, `start-lab.ps1`, `win-lab.ps1` – build and start the lab build on a Windows VM
- `session.sh`, `hd-session.sh`, `fps.sh`, `clip.sh`, `itask.sh` – start sessions and measure
- `q.sh`, `q11.sh`, `w.sh`, `w11.sh`, `l.sh`, `shot.sh` – click and take screenshots on the VMs
- `mint.sh`, `grant.sh`, `lab-admin.sh`, `web-accept.sh` – test sign-in and rights (need `../lab-session.py`
  on the server and a dedicated test account; remove both after testing)
- `xfer-server.py`, `linux-check.sh` – file transfer and checks for the Linux VM
- `webdev/server.mjs` – serves the browser viewer on `http://localhost:5173/remote-next/` against the live services
