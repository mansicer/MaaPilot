"""Harmless native cron probe. No model calls, game access or credential output."""
import json
import os
from pathlib import Path
import subprocess
import sys


QUERY = """import json, os
from pathlib import Path
from maapilot.crew import maa
from maapilot.oscompat import procs
dirs = {}
for kind in ('data', 'cache', 'state', 'config', 'resource'):
    result = procs.run_text([maa.maa_bin(), 'dir', kind], timeout=15, env=maa.env())
    result.check_returncode()
    dirs[kind] = result.stdout.strip()
print(json.dumps({'directories': dirs, 'resource_exists': Path(dirs['resource']).is_dir(),
                  'environment': {k: os.environ.get(k) for k in
                    ('MAA_DATA_DIR', 'MAA_CACHE_DIR', 'MAA_STATE_DIR', 'MAA_CONFIG_DIR')}}))
"""


def run(spec):
    result = {"nonce": spec["nonce"], "lane": spec["lane"], "ok": True}
    if spec["lane"] == "runtime":
        child = subprocess.run([spec["python"], "-c", QUERY], cwd=spec["workdir"],
                               capture_output=True, text=True, encoding="utf-8", timeout=85)
        if child.returncode:
            result["ok"] = False
            result["error"] = "maa_directory_query_failed"  # Never echo subprocess stderr.
        else:
            result.update(json.loads(child.stdout))
    path = Path(spec["result"])
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
    if spec["lane"] == "failure":
        raise SystemExit("MAAPILOT_PROBE_FAILURE: intentional failure; no game was run")
    print("MaaPilot 通知测试：成功；未操作游戏。" if spec["lane"] == "success"
          else "MaaPilot runtime probe completed")


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
