#!/usr/bin/env python3
"""GLASSDECK data exporter — reads the feeder's EXISTING data stores (read-only)
and publishes them as JSON files for the GLASSDECK dashboard.

Design principles (do not violate):
  * READ-ONLY against every existing store: RRD archives, /sys, /proc.
    graphs1090, tar1090, and the adsb.im UI keep working untouched.
  * No second copy of truth: we export views, we never import/own data.
  * Atomic writes (tmp + rename) so the dashboard never reads a half file.
  * Uninstall = remove two cron lines + this file. Nothing else changes.

Install (as root on the feeder):
  cp gd_exporter.py /opt/adsb/gd_exporter.py && chmod +x /opt/adsb/gd_exporter.py
  crontab -e   # add:
    * * * * *    /opt/adsb/gd_exporter.py system  /path/to/webroot/gd-data
    */5 * * * *  /opt/adsb/gd_exporter.py history /path/to/webroot/gd-data
(webroot = the directory the GLASSDECK dashboard page is served from)
"""
import json, os, re, subprocess, sys, tempfile

RRD_BASE = "/run/collectd/localhost"
CONTAINER = "ultrafeeder"

# metric -> RRD file (append ":dsname" when the data source is not "value");
# key names must match HIST_CFGS in the dashboard
D1090 = f"{RRD_BASE}/dump1090-localhost"
HISTORY_METRICS = {
    "msg_local":      f"{D1090}/dump1090_messages-local_accepted.rrd",
    "msg_remote":     f"{D1090}/dump1090_messages-remote_accepted.rrd",
    "positions":      f"{D1090}/dump1090_messages-positions.rrd",
    "aircraft_total": f"{D1090}/dump1090_aircraft-recent.rrd:total",
    "aircraft_gps":   f"{D1090}/dump1090_gps-recent.rrd",
    "aircraft_mlat":  f"{D1090}/dump1090_mlat-recent.rrd",
    "aircraft_tisb":  f"{D1090}/dump1090_tisb-recent.rrd",
    "sig_mean":       f"{D1090}/dump1090_dbfs-signal.rrd",
    "sig_median":     f"{D1090}/dump1090_dbfs-median.rrd",
    "sig_q1":         f"{D1090}/dump1090_dbfs-quart1.rrd",
    "sig_q3":         f"{D1090}/dump1090_dbfs-quart3.rrd",
    "sig_peak":       f"{D1090}/dump1090_dbfs-peak_signal.rrd",
    "noise":          f"{D1090}/dump1090_dbfs-noise.rrd",
    "range_max":      f"{D1090}/dump1090_range-max_range.rrd",
    "range_median":   f"{D1090}/dump1090_range-median.rrd",
    "range_q1":       f"{D1090}/dump1090_range-quart1.rrd",
    "range_q3":       f"{D1090}/dump1090_range-quart3.rrd",
    "tracks_all":     f"{D1090}/dump1090_tracks-all.rrd",
    "tracks_single":  f"{D1090}/dump1090_tracks-single_message.rrd",
    "gain":           f"{D1090}/dump1090_misc-gain_db.rrd",
    "temp":           f"{RRD_BASE}/table-localhost/gauge-cpu_temp.rrd",
    "mem_used":       f"{RRD_BASE}/system_stats/memory-used.rrd",
    "cpu_idle":       f"{RRD_BASE}/aggregation-cpu-average/cpu-idle.rrd",
}
# window name -> (rrd start, step seconds)
WINDOWS = {"6h": ("-6h", 300), "24h": ("-24h", 900), "8d": ("-8d", 3600), "34d": ("-34d", 14400)}


def atomic_write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.chmod(tmp, 0o644)  # mkstemp defaults to 0600 — the container's nginx must be able to read
    os.replace(tmp, path)


def rrd_xport(rrd, start, step):
    ds = "value"
    if ":" in rrd.rsplit("/", 1)[-1]:
        rrd, ds = rrd.rsplit(":", 1)
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "rrdtool", "xport", "--json",
         "-s", start, "--step", str(step),
         f"DEF:a={rrd}:{ds}:AVERAGE", "XPORT:a:v"],
        capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return None
    d = json.loads(out.stdout)
    vals = [row[0] for row in d.get("data", [])]
    meta = d.get("meta", {})
    return {"start": meta.get("start"), "step": meta.get("step"),
            "v": [None if v is None else round(v, 3) for v in vals]}


def export_history(out_dir):
    hist = {}
    for name, rrd in HISTORY_METRICS.items():
        hist[name] = {}
        for wname, (start, step) in WINDOWS.items():
            series = rrd_xport(rrd, start, step)
            if series:
                hist[name][wname] = series
    atomic_write(os.path.join(out_dir, "history.json"), hist)


def read_first(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def http_json(url, timeout=4):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


# adsb.im's own status APIs — the same data its "You are feeding" table shows
AGG_STATUS_KEYS = {"adsb.lol": "adsblol", "adsb.fi": "adsbfi", "airplanes.live": "alive"}


def export_system(out_dir):
    temp_raw = read_first("/sys/class/thermal/thermal_zone0/temp", "0")
    load1 = float(read_first("/proc/loadavg", "0 0 0").split()[0])
    mem = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        mem[k] = int(v.strip().split()[0]) * 1024
    disk = os.statvfs("/")
    net = {}
    for line in open("/proc/net/dev"):
        m = re.match(r"\s*(wlan0|eth0):\s*(\d+)(?:\s+\d+){7}\s+(\d+)", line)
        if m:
            net[m.group(1)] = {"rx": int(m.group(2)), "tx": int(m.group(3))}
    uptime = float(read_first("/proc/uptime", "0 0").split()[0])
    base = http_json("http://127.0.0.1:80/api/base_info")
    uplinks = {}
    for name, key in AGG_STATUS_KEYS.items():
        d = http_json(f"http://127.0.0.1:80/api/status/{key}")
        if d and "0" in d:
            uplinks[name] = {"beast": d["0"].get("beast"), "mlat": d["0"].get("mlat")}
    atomic_write(os.path.join(out_dir, "system.json"), {
        "base": base,
        "uplinks": uplinks,
        "ts": int(__import__("time").time()),
        "tempC": round(int(temp_raw) / 1000, 1),
        "load1": load1,
        "memUsedPct": round((mem["MemTotal"] - mem["MemAvailable"]) / mem["MemTotal"] * 100, 1),
        "diskUsedPct": round((1 - disk.f_bavail / disk.f_blocks) * 100, 1),
        "net": net,
        "uptimeSec": int(uptime),
    })


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("system", "history", "all"):
        sys.exit("usage: gd_exporter.py system|history|all <out_dir>")
    mode, out_dir = sys.argv[1], sys.argv[2]
    if mode in ("system", "all"):
        export_system(out_dir)
    if mode in ("history", "all"):
        export_history(out_dir)
