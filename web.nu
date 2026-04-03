#!/usr/bin/env nu

def _python_path [] {
  if ($nu.os-info.name | str downcase | str contains "windows") {
    ".venv\\Scripts\\python.exe"
  } else {
    ".venv/bin/python"
  }
}

def main [command: string = "status", ...rest] {
  let py = (_python_path)
  if not ($py | path exists) {
    print $"[web] chyba: chybí ($py)"
    exit 2
  }
  ^$py -X utf8 scripts/webctl.py $command ...$rest
}
