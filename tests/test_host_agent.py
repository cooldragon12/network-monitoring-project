import threading

import pytest

from agent import agent as host_agent
from monitoring.models import HostEvent, RemoteHost

MEMINFO = "MemTotal:        8000000 kB\nMemFree:          500000 kB\nMemAvailable:    2000000 kB\n"
STAT_1 = "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3 4\n"
STAT_2 = "cpu  150 0 150 900 0 0 0 0 0 0\n"
VM_STAT = (
    "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
    "Pages free:                               10000.\n"
    "Pages active:                            100000.\n"
    "Pages wired down:                         50000.\n"
    "Pages occupied by compressor:             50000.\n"
)


def test_linux_parsers():
    assert host_agent.parse_meminfo(MEMINFO) == 75.0
    assert host_agent.parse_proc_stat(STAT_1) == (800, 1000)
    assert host_agent.cpu_percent_between((800, 1000), host_agent.parse_proc_stat(STAT_2)) == 50.0
    assert host_agent.cpu_percent_between((1, 1), (1, 1)) is None


def test_macos_parsers():
    total = 200000 * 16384 * 2  # used pages are half of RAM
    assert host_agent.parse_vm_stat(VM_STAT, total) == 50.0
    assert host_agent.parse_top_cpu("CPU usage: 5.12% user, 8.33% sys, 86.6% idle") == 13.4
    assert host_agent.parse_boottime("{ sec = 1000, usec = 0 } Thu", now=4600.0) == 3600


def test_config(tmp_path):
    conf = tmp_path / "agent.env"
    conf.write_text("NETMON_SERVER_URL=http://x:8000/\nNETMON_TOKEN=abc\nNETMON_INTERVAL=5\n")
    cfg = host_agent.load_config(str(conf), env={})
    assert cfg["server"] == "http://x:8000" and cfg["token"] == "abc"
    assert cfg["interval"] == 10  # floor
    with pytest.raises(host_agent.ConfigError):
        host_agent.load_config(env={"NETMON_SERVER_URL": "http://x"})


def test_collect_has_core_fields():
    metrics = host_agent.Collector().collect()
    assert metrics["agent_version"] == host_agent.VERSION
    assert 0 <= metrics["disk_percent"] <= 100


def test_run_forever_backs_off_then_uses_interval():
    calls, waits = [], []

    def send(cfg, metrics):
        calls.append(metrics)
        if len(calls) <= 2:
            raise ConnectionRefusedError("down")
        return {}

    class FakeStop:
        def is_set(self):
            return len(waits) >= 4

        def wait(self, seconds):
            waits.append(seconds)

    class StaticCollector:
        def collect(self):
            return {"hostname": "h"}

    cfg = {"server": "http://x", "token": "t", "interval": 60, "disk_path": "/", "ca_file": None}
    host_agent.run_forever(cfg, FakeStop(), collector=StaticCollector(), send=send)
    assert waits == [5.0, 10.0, 60, 60]


@pytest.mark.django_db(transaction=True)
def test_checkin_end_to_end(live_server):
    host = RemoteHost(name="nas", expected_interval_seconds=120)
    token = host.set_new_token()
    host.save()
    cfg = {"server": live_server.url, "token": token, "interval": 60, "disk_path": "/", "ca_file": None}
    resp = host_agent.send_checkin(cfg, {"hostname": "nas.lan", "agent_version": "1.0.0", "cpu_percent": 3.5,
                                         "mem_percent": 41.0, "disk_percent": 70.0, "uptime_seconds": 99})
    assert resp == {"status": "ok", "host": "nas", "expected_interval_seconds": 120}
    host.refresh_from_db()
    assert host.status == "online" and host.cpu_percent == 3.5 and host.reported_hostname == "nas.lan"
    assert HostEvent.objects.filter(host=host, event_type="online").exists()

    cfg["token"] = "wrong"
    with pytest.raises(host_agent.CheckinError) as excinfo:
        host_agent.send_checkin(cfg, {})
    assert excinfo.value.status == 401
