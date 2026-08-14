#!/usr/bin/env python3
"""
Проверка архитектурных правил MORPHEUS — без зависимостей, запускается где угодно.

    python3 tools/check_architecture.py            # проверить всё
    python3 tools/check_architecture.py --baseline # записать текущее состояние как базу

Три правила, каждое родилось из реальной ошибки в этом проекте:

1. ТОЧКА ВХОДА — НЕ БИБЛИОТЕКА. `app/main.py` запускает сервис; он не должен быть
   местом, откуда все берут `connect_postgres`, `get_agent_credentials` и прочую
   инфраструктуру. Из-за этого в MYRMIDON образовалось 10 циклов импорта, и каждый
   лечили ленивым импортом внутри функции (118 штук по проекту). Итог был не
   косметическим: чтение ветки внутри задачи полезло за сессией того же агента,
   чей лок держало, и повесило единственный потребитель очереди.

2. НЕТ ЦИКЛОВ. Цикл импорта — это всегда отложенная поломка: модуль работает, пока
   его импортируют в правильном порядке.

3. ИМПОРТЫ НАВЕРХУ. Импорт внутри функции прячет зависимость от инструментов и от
   читателя; он допустим только как осознанное исключение (тяжёлая загрузка модели,
   разрыв цикла на время рефакторинга) и должен быть помечен `# lazy:` с причиной.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ("daedalus/app", "orpheus/app", "myrmidon/app", "huginn/app")
BASELINE = ROOT / "tools" / "architecture_baseline.json"


def module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    return str(rel).replace(os.sep, ".").replace(".__init__", "")


def scan(service: Path) -> tuple[dict[str, set[str]], list[str]]:
    """Граф импортов внутри сервиса + список ленивых импортов."""
    graph: dict[str, set[str]] = defaultdict(set)
    lazy: list[str] = []
    for path in sorted(service.rglob("*.py")):
        mod = module_name(path, service)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            print(f"  !! не разобран {path}: {exc}")
            continue
        toplevel = {id(n) for n in tree.body}
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
                targets.append(node.module[4:] if node.module.startswith("app.") else "")
            elif isinstance(node, ast.Import):
                targets += [a.name[4:] for a in node.names if a.name.startswith("app.")]
            if not targets:
                continue
            graph[mod].update(t for t in targets if t)
            if id(node) not in toplevel:
                line = path.read_text(encoding="utf-8").splitlines()[node.lineno - 1]
                if "# lazy:" not in line:
                    lazy.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return graph, lazy


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    found: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def walk(node: str, path: list[str]) -> None:
        for nxt in sorted(graph.get(node, ())):
            if nxt in path:
                cycle = path[path.index(nxt):] + [nxt]
                key = tuple(sorted(set(cycle)))
                if key not in seen:
                    seen.add(key)
                    found.append(cycle)
            elif len(path) < 6:
                walk(nxt, path + [nxt])

    for node in sorted(graph):
        walk(node, [node])
    return found


def main() -> int:
    write_baseline = "--baseline" in sys.argv
    report: dict[str, dict] = {}
    failures: list[str] = []

    for service in SERVICES:
        root = ROOT / service
        if not root.exists():
            continue
        graph, lazy = scan(root)
        cycles = find_cycles(graph)
        importers_of_main = sorted(m for m, deps in graph.items() if "main" in deps and m != "main")

        report[service] = {"cycles": len(cycles), "lazy_imports": len(lazy),
                           "importers_of_main": len(importers_of_main)}
        print(f"=== {service}")
        print(f"    модулей: {len(graph)}  циклов: {len(cycles)}  "
              f"ленивых импортов: {len(lazy)}  импортируют main: {len(importers_of_main)}")
        for cycle in cycles[:5]:
            print("      цикл: " + " -> ".join(cycle))
        if importers_of_main:
            print("      main как библиотека: " + ", ".join(importers_of_main[:6]))

    if write_baseline:
        BASELINE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nБаза записана в {BASELINE.relative_to(ROOT)}")
        return 0

    if not BASELINE.exists():
        print("\nБазы нет — запустите с --baseline, чтобы зафиксировать текущее состояние.")
        return 0

    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    for service, now in report.items():
        was = base.get(service, {})
        for metric, value in now.items():
            before = was.get(metric)
            if before is not None and value > before:
                failures.append(f"{service}: {metric} было {before}, стало {value}")

    if failures:
        print("\nСТАЛО ХУЖЕ — правило «не добавлять нового долга» нарушено:")
        for f in failures:
            print("  ✗ " + f)
        return 1
    print("\nОК: новых циклов, ленивых импортов и зависимостей от main не добавилось.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
