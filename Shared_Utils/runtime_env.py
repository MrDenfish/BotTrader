import os, pathlib

def running_in_docker() -> bool:
    val = os.getenv("DOCKER_ENV", "")
    if val:
        return val.lower() == "true"
    if pathlib.Path("/.dockerenv").exists():
        return True
    try:
        with open("/proc/1/cgroup", "rt") as f:
            txt = f.read()
            if "docker" in txt or "containerd" in txt:
                return True
    except Exception:
        pass
    return False
