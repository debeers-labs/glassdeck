#!/usr/bin/env python3
"""Tests for the agent installer — the half of GLASSDECK that runs as root on
other people's Raspberry Pis.

Run: python3 test_gd_install.py

Nothing here touches a real feeder: /opt/adsb/config/{config.json,.env} and the
UUID file are redirected into a temp dir, /expert is a local stub, and the hub
call is recorded rather than made. gd_install.py reads no paths at import time,
which is what makes that possible — keep it that way.

GD_INSTALL_DIR points the import at a different copy of agent/gd_install.py, so
a deliberately broken copy can be run against this same suite (mutation
testing). Every assertion below was checked that way: each one fails when the
defect it is aimed at is put back.
"""
import importlib.util, json, os, shutil, stat, sys, tempfile, time, types

SRC = os.path.join(os.environ.get("GD_INSTALL_DIR",
                                  os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent")),
                   "gd_install.py")
spec = importlib.util.spec_from_file_location("gd_install", SRC)
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)

fails = []
group = ""


def ok(cond, label, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (" -> " + str(detail) if detail else ""))
    if not cond:
        fails.append(group + ": " + label)


def section(name):
    global group
    group = name
    print("\n" + name)


def text(path):
    """File body, or '' if it isn't there — so one missing artefact does not
    abort the run before the later assertions have had their say."""
    try:
        return open(path).read().strip()
    except OSError:
        return ""


def call(fn, *a):
    """Run product code that is expected to return, not raise. A raise is a
    failure of this assertion, not a reason to stop testing."""
    try:
        return fn(*a)
    except Exception as e:
        return "raised %s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- feeder stub

TMP = tempfile.mkdtemp(prefix="gd-install-")
HOST = gd.GLASSDECK_HOST
OLD_ID = "17691ba9-4f7f-4dc2-9f11-8e2c6b0a1d34"   # shapes only; not live ids
OTHER_ID = "6d40af7d-2c88-41ba-b0f7-5a19e3c47f02"

# no real sleeping: set_network waits 45 x 2s for the app to come back
gd.time = types.SimpleNamespace(sleep=lambda s: None, time=time.time)
# and no real DNS: the join path resolves the hub only to print a warning
gd.socket = types.SimpleNamespace(gethostbyname=lambda h: "203.0.113.1")

posted = []          # every value handed to /expert
hub_calls = []       # every (old, new, box-at-the-time) handed to the hub


def feeder(config=None, env=None, uuid=None):
    """Reset to a fresh feeder. `config`/`env` are file bodies; None = absent."""
    del posted[:], hub_calls[:]
    for name in os.listdir(TMP):
        os.remove(os.path.join(TMP, name))
    gd.CONFIG_JSON = os.path.join(TMP, "config.json")
    gd.ENV_PATH = os.path.join(TMP, ".env")
    gd.UUID_FILE = os.path.join(TMP, "UUID")
    if config is not None:
        open(gd.CONFIG_JSON, "w").write(config)
    if env is not None:
        open(gd.ENV_PATH, "w", newline="").write(env)
    if uuid is not None:
        open(gd.UUID_FILE, "w").write(uuid + "\n")


def box(value):
    """A feeder whose Expert extra-env box holds `value` (newer-image layout)."""
    feeder(config=json.dumps({gd.EXTRA_ENV_KEY: value}), env="AF_WEBPORT=80\n")


def expert_ok(value):
    """The adsb.im app as it behaves when it works: the box becomes `value`."""
    posted.append(value)
    open(gd.CONFIG_JSON, "w").write(json.dumps({gd.EXTRA_ENV_KEY: value}))


def expert_dead(value):
    """The app took the POST and changed nothing (restart hung, wrong port,
    500 behind the redirect) — the failure --rotate must not mistake for done."""
    posted.append(value)


def hub_stub(result):
    def tell(old, new):
        hub_calls.append((old, new, gd.read_extra_env()))
        return result
    return tell


# ------------------------------------------------- M1: "unreadable" must show

section("read_extra_env signals failure distinctly from empty")

feeder()                                        # neither store exists
ok(gd.read_extra_env() is None, "both stores absent -> None (not '')",
   repr(gd.read_extra_env()))

box("READSB_NET_CONNECTOR=%s,30004,beast_reduce_plus_out\n" % HOST)
ok(HOST in gd.read_extra_env(), "config.json value is returned")

feeder(config=json.dumps({"OTHER": 1}), env="AF_WEBPORT=80\n")
ok(gd.read_extra_env() == "", "readable in both, key in neither -> '' (a genuinely empty box)",
   repr(gd.read_extra_env()))

feeder(config="{ this is not json", env="AF_WEBPORT=80\n")
ok(gd.read_extra_env() is None, "config.json corrupt, key not in .env -> None",
   repr(gd.read_extra_env()))

feeder(config="{ this is not json",
       env="AF_WEBPORT=80\n%s=READSB_NET_CONNECTOR=%s,30004,beast\n" % (gd.EXTRA_ENV_KEY, HOST))
ok(HOST in (gd.read_extra_env() or ""), ".env still rescues a corrupt config.json")

feeder(config=json.dumps({"OTHER": 1}))         # no .env at all
ok(gd.read_extra_env() is None, "config.json readable but no .env -> None")

feeder(env="%s=A=1\r\nB=2\nAF_WEBPORT=80\n" % gd.EXTRA_ENV_KEY)
ok(gd.read_extra_env() == "A=1\r\nB=2", "CRLF-continued .env value still parses",
   repr(gd.read_extra_env()))


section("an unreadable box is never rewritten")

CUSTOM = ("READSB_NET_CONNECTOR=someagg.example,30004,beast_reduce_plus_out\r\n"
          "READSB_EXTRA_ARGS=--gain 42\r\nUF_CUSTOM=keep-me")

feeder(config="{ torn write", env="AF_WEBPORT=80\n")     # box unreadable
gd.post_extra_env = expert_ok
ok(call(gd.set_network, True) is False, "--join refuses when the box cannot be read")
ok(posted == [], "nothing was posted to /expert", posted)

feeder(config="{ torn write", env="AF_WEBPORT=80\n")
try:
    gd.rotate_uuid()
    ok(False, "--rotate exits when the box cannot be read")
except SystemExit as e:
    ok("cannot read" in str(e), "--rotate exits when the box cannot be read", e)
ok(posted == [] and hub_calls == [], "…and posts nothing, tells the hub nothing")


section("a box that goes unreadable MID-CONFIRMATION is 'not yet', not a crash")

# set_network polls the box 45x while the app rewrites config.json and restarts
# the containers — the one window in this program where a torn read is likely.
# "Unreadable" must mean keep waiting. It must not raise out of set_network:
# rotate_uuid() calls set_network AFTER overwriting UUID with the new id, so an
# exception there skips the rollback and leaves the file holding an id the
# feeder is not sending — the exact identity fork the rest of this suite exists
# to prevent.
def expert_torn(landing_tick):
    """The app truncates config.json on POST and finishes it `landing_tick`
    polls later. Ticks are counted from the POST, so reads set_network makes
    before it (glassdeck_connector -> feeder_uuid) do not consume the window."""
    state = {"ticks": 0, "value": None, "posted": False}
    real = gd.read_extra_env

    def post(value):
        posted.append(value)
        state["value"], state["posted"] = value, True
        open(gd.CONFIG_JSON, "w").write('{"_ADSBIM_STATE_EXTR')      # torn write
    def read():
        if state["posted"]:
            state["ticks"] += 1
            if state["ticks"] == landing_tick:
                open(gd.CONFIG_JSON, "w").write(json.dumps({gd.EXTRA_ENV_KEY: state["value"]}))
        return real()
    return post, read, state


real_read = gd.read_extra_env
box(CUSTOM)
gd.post_extra_env, gd.read_extra_env, st8 = expert_torn(3)
r = call(gd.set_network, True)
gd.read_extra_env = real_read
ok(r is True, "a join whose write lands after two unreadable polls still confirms", r)
ok(gd.glassdeck_connector() in gd.read_extra_env(), "…and the box really carries the connector")
ok("UF_CUSTOM=keep-me" in gd.read_extra_env(), "…with the user's settings intact")

stale_for_torn = "READSB_NET_CONNECTOR=%s,30004,beast_reduce_plus_out,uuid=%s" % (HOST, OLD_ID)
box(stale_for_torn)
open(gd.UUID_FILE, "w").write(OTHER_ID + "\n")   # switching ids, so `want` != the box
gd.post_extra_env, gd.read_extra_env, _ = expert_torn(10 ** 6)   # never lands
r = call(gd.set_network, True)
gd.read_extra_env = real_read
ok(r is False, "a box that never becomes readable is reported as NOT confirmed", r)

box(stale_for_torn)
open(gd.UUID_FILE, "w").write(OLD_ID + "\n")
gd.post_extra_env, gd.read_extra_env, _ = expert_torn(10 ** 6)
gd.tell_hub = hub_stub(True)
try:
    gd.rotate_uuid()
    ok(False, "--rotate rolls back rather than dying inside set_network")
except SystemExit as e:
    ok("NOT changed" in str(e), "--rotate rolls back rather than dying inside set_network", e)
except Exception as e:
    ok(False, "--rotate rolls back rather than dying inside set_network",
       "raised %s: %s" % (type(e).__name__, e))
gd.read_extra_env = real_read
ok(text(gd.UUID_FILE) == OLD_ID,
   "…leaving UUID on the id the feeder is actually sending", text(gd.UUID_FILE))
ok(hub_calls == [], "…and the hub was told nothing", hub_calls)


# --------------------------------------- H1: confirm the connector, not the host

section("set_network confirms what it asked for")

box(CUSTOM)
gd.post_extra_env = expert_ok
ok(call(gd.set_network, True) is True, "join succeeds when /expert applies it")
after = gd.read_extra_env()
ok(gd.glassdeck_connector() in after, "the box now carries THIS feeder's connector", after)
ok("UF_CUSTOM=keep-me" in after and "--gain 42" in after,
   "the user's other Expert settings survived", after)
ok("someagg.example" in after, "another aggregator's connector survived", after)

# The precondition trap: the host is already in the box before the write, so a
# check for the host alone reports success even when nothing was applied.
stale = "READSB_NET_CONNECTOR=%s,30004,beast_reduce_plus_out,uuid=%s" % (HOST, OLD_ID)
box(stale)
open(gd.UUID_FILE, "w").write(OTHER_ID + "\n")      # we are switching to OTHER_ID
gd.post_extra_env = expert_dead
ok(call(gd.set_network, True) is False,
   "a dead /expert is NOT reported as joined just because the host is present")
ok(posted and OTHER_ID in posted[0], "…the attempt did carry the new id", posted[:1])

box(stale)
gd.post_extra_env = expert_dead
ok(call(gd.set_network, False) is False, "a dead /expert is NOT reported as left")

box(stale)
gd.post_extra_env = expert_ok
ok(call(gd.set_network, False) is True, "leave succeeds when /expert applies it")
ok(HOST not in gd.read_extra_env(), "…and the connector is really gone")


# ------------------------------- H1/H2: --rotate against a failing /expert

section("--rotate against a failing /expert")

box(stale)
open(gd.UUID_FILE, "w").write(OLD_ID + "\n")
gd.post_extra_env = expert_dead
gd.tell_hub = hub_stub(True)
try:
    gd.rotate_uuid()
    ok(False, "rotate exits rather than claiming success")
except SystemExit as e:
    ok("NOT changed" in str(e), "rotate exits rather than claiming success", e)
ok(hub_calls == [], "the hub was NOT told to retire an id the feeder is still sending",
   hub_calls)
ok(text(gd.UUID_FILE) == OLD_ID, "the working id was put back on file", text(gd.UUID_FILE))
ok(text(gd.UUID_FILE + ".prev") == OLD_ID, "the outgoing id is remembered for the re-run",
   text(gd.UUID_FILE + ".prev"))

section("--rotate when /expert works")

box(stale)
open(gd.UUID_FILE, "w").write(OLD_ID + "\n")
gd.post_extra_env = expert_ok
gd.tell_hub = hub_stub(True)
gd.rotate_uuid()
new_id = text(gd.UUID_FILE)
ok(len(hub_calls) == 1, "the hub was told once", hub_calls)
told_old, told_new, box_then = hub_calls[0]
ok(told_old == OLD_ID and told_new == new_id, "…with the real old and new ids", hub_calls[0])
ok(new_id != OLD_ID, "the id actually changed")
ok(told_new in box_then and told_old not in box_then,
   "…and only AFTER the connector demonstrably carried the new id", box_then)
ok(not os.path.exists(gd.UUID_FILE + ".prev"), "the .prev debt is cleared on success")
ok(stat.S_IMODE(os.stat(gd.UUID_FILE).st_mode) == 0o600, "the new id is not world-readable")

section("--rotate when the hub is unreachable")

box(stale)
open(gd.UUID_FILE, "w").write(OLD_ID + "\n")
gd.post_extra_env = expert_ok
gd.tell_hub = hub_stub(False)
gd.rotate_uuid()
ok(text(gd.UUID_FILE) not in (OLD_ID, ""), "the feeder keeps the new, working id")
ok(text(gd.UUID_FILE + ".prev") == OLD_ID,
   "the un-retired old id is kept so a re-run can still name it", text(gd.UUID_FILE + ".prev"))

# A re-run must finish that debt against the id the hub never heard about — and
# that id is the one from the FIRST rotation, so only a fresh log proves it.
del hub_calls[:]
gd.tell_hub = hub_stub(True)
gd.rotate_uuid()
ok(hub_calls and hub_calls[0][0] == OLD_ID,
   "a re-run retires the ORIGINAL leaked id first, not the one from last time",
   hub_calls)


# ---------------------------- H8b: uninstall + reinstall must not fork the id

section("identity survives uninstall + reinstall")

box("READSB_NET_CONNECTOR=%s,30004,beast_reduce_plus_out,uuid=%s" % (HOST, OLD_ID))
os.remove(gd.UUID_FILE) if os.path.exists(gd.UUID_FILE) else None   # what uninstall.sh does
ok(gd.feeder_uuid() == OLD_ID,
   "a reinstall adopts the id the feeder is still sending, instead of minting a new one",
   gd.feeder_uuid())
ok(text(gd.UUID_FILE) == OLD_ID, "…and writes it back to UUID", text(gd.UUID_FILE))
ok(os.path.exists(gd.UUID_FILE) and stat.S_IMODE(os.stat(gd.UUID_FILE).st_mode) == 0o600,
   "…mode 600")

# and --rotate after that reinstall must name the id the hub actually knows
gd.post_extra_env = expert_ok
gd.tell_hub = hub_stub(True)
call(gd.rotate_uuid)
ok(hub_calls and hub_calls[0][0] == OLD_ID,
   "--rotate after a reinstall offers the hub an id it has actually issued", hub_calls)

box("READSB_NET_CONNECTOR=someagg.example,30004,beast,uuid=%s" % OTHER_ID)
ok(gd.uuid_from_connector() == "",
   "another aggregator's uuid is never adopted as ours", gd.uuid_from_connector())
minted = gd.feeder_uuid()
ok(minted not in (OTHER_ID, OLD_ID) and len(minted) == 36,
   "with no GLASSDECK connector a fresh id is minted", minted)

box(CUSTOM)
os.remove(gd.UUID_FILE) if os.path.exists(gd.UUID_FILE) else None
ok(gd.uuid_from_connector() == "", "a connector-less box yields no id")

feeder()      # unreadable box: still no crash, and no adoption
ok(gd.uuid_from_connector() == "", "an unreadable box yields no id, not an exception")


# ------------------------------------------------------------------- report

shutil.rmtree(TMP, ignore_errors=True)
print()
if fails:
    print("%d FAILED:" % len(fails))
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("all gd_install checks passed")
