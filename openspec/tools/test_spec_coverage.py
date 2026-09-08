"""Покрытие сценариев OpenSpec тестами: у каждого `#### Scenario:` есть проверка.

Это не code coverage. Знаменатель — не строки кода, а список сценариев из спек;
числитель — сценарии, на которые сослался хоть один тест маркером
`@pytest.mark.spec("<capability>/<Requirement>/<Scenario>")` либо запись в `MANUAL`.

Что здесь проверяется:
  * маркер не ссылается на сценарий, которого в спеках нет (сценарий переименовали
    или требование удалили — тест краснеет, и видно, какой именно);
  * `MANUAL` не ссылается ни на несуществующий сценарий, ни на пропавший протокол;
  * все сценарии capability из `TRACKED`, уже уехавших в `openspec/specs/`,
    покрыты. Пока change не заархивирован, его сценарии — только отчёт: писать
    тесты на нереализованные блоки нечем, и красный тут был бы шумом. Гейт
    включается сам в момент архивации, когда требование становится постоянным.

Отчёт по активным change'ам печатается всегда:
    python -m pytest tests/test_spec_coverage.py -s -q

Установка: положи этот файл и `spec_coverage_data.py` рядом в тестовую папку
проекта, заполни `TRACKED` и зарегистрируй маркер `spec` (см. `README.md`
рядом с этим файлом в репозитории openspec-in-action).
"""

from __future__ import annotations

import importlib.util
import re
from collections import defaultdict
from pathlib import Path

import pytest  # noqa: F401  — маркер `spec` живёт в других тестах, здесь нужен только импорт-контракт


def _find_repo_root(start: Path) -> Path:
    """Поднимается вверх до первой папки, в которой лежит `openspec/config.yaml`.

    Поиск, а не фиксированное число `parents[N]`: файл кладут и в `tests/`,
    и в `backend/tests/`, и в `services/api/tests/`. Жёсткий индекс пришлось бы
    править при каждом переезде тестовой папки, и ошибка проявилась бы не
    падением, а тихо пустым списком сценариев — то есть зелёным гейтом,
    который ничего не сторожит.
    """
    for candidate in (start, *start.parents):
        if (candidate / "openspec" / "config.yaml").is_file():
            return candidate
    raise RuntimeError(
        f"не найден корень репозитория: ни в {start}, ни выше нет openspec/config.yaml"
    )


TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = _find_repo_root(TESTS_DIR)
OPENSPEC = REPO_ROOT / "openspec"


def _load_data():
    """Загружает `spec_coverage_data.py` по пути, а не импортом по имени.

    Обычный `from spec_coverage_data import ...` зависит от того, что pytest
    положил в `sys.path`, а это решают `rootdir`, режим импорта и наличие
    `__init__.py` в тестовой папке. Загрузка по пути не зависит ни от чего:
    данные лежат ровно рядом с этим файлом.
    """
    path = Path(__file__).with_name("spec_coverage_data.py")
    spec = importlib.util.spec_from_file_location("spec_coverage_data", path)
    if spec is None or spec.loader is None:  # pragma: no cover — сломанная установка
        raise RuntimeError(f"не удалось загрузить {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DATA = _load_data()
TRACKED: set[str] = _DATA.TRACKED
MANUAL: dict[str, str] = _DATA.MANUAL

# `## REMOVED Requirements` описывает то, чего в системе больше нет: его сценарии
# (если автор их оставил) покрывать нечем и не нужно.
_SKIP_SECTIONS = {"REMOVED Requirements"}


def _parse(path: Path, capability: str) -> set[str]:
    """Возвращает пути 'capability/Requirement/Scenario' из одного файла спеки."""
    found: set[str] = set()
    requirement: str | None = None
    skipping = False

    for line in path.read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^## (.+)", line):
            skipping = match.group(1).strip() in _SKIP_SECTIONS
            requirement = None
        elif match := re.match(r"^### Requirement: (.+)", line):
            requirement = match.group(1).strip()
        elif match := re.match(r"^#### Scenario: (.+)", line):
            if not skipping and requirement:
                found.add(f"{capability}/{requirement}/{match.group(1).strip()}")

    return found


def archived_scenarios() -> set[str]:
    """Возвращает пути сценариев из постоянных спек `openspec/specs/`."""
    return {
        path
        for spec in (OPENSPEC / "specs").glob("*/spec.md")
        for path in _parse(spec, spec.parent.name)
    }


def change_scenarios() -> dict[str, set[str]]:
    """Возвращает пути сценариев по каждому активному change'у (архив не входит)."""
    by_change: dict[str, set[str]] = defaultdict(set)
    for spec in (OPENSPEC / "changes").glob("*/specs/*/spec.md"):
        change = spec.parents[2].name
        by_change[change] |= _parse(spec, spec.parent.name)
    return dict(by_change)


def marked() -> set[str]:
    """Возвращает пути, на которые сослался хоть один тест маркером `spec`.

    Маркеры читаются регуляркой по исходникам, а не через `item.iter_markers`:
    так покрытие считается без запуска коллекции и не зависит от того, доступна
    ли БД. Цена — регулярка не отличает маркер от его упоминания в тексте, и
    параметризованные тесты видны как один маркер. Для сверки двух множеств
    строк этого достаточно; свой файл из выборки исключён, иначе примеры в
    докстринге выше засчитались бы как покрытие.
    """
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TESTS_DIR.glob("test_*.py")
        if path.name != Path(__file__).name
    )
    return set(re.findall(r'@pytest\.mark\.spec\(\s*"([^"]+)"', sources))


def covered() -> set[str]:
    return marked() | set(MANUAL)


def _tracked(paths: set[str]) -> set[str]:
    return {path for path in paths if path.split("/", 1)[0] in TRACKED}


# ----------------------------------------------------------------- проверки


def test_markers_point_at_existing_scenarios():
    """Переименовали сценарий или удалили требование — маркер должен покраснеть."""
    known = archived_scenarios() | {path for paths in change_scenarios().values() for path in paths}
    unknown = sorted(marked() - known)
    assert not unknown, "маркеры ссылаются на сценарии, которых нет в спеках:\n  " + "\n  ".join(unknown)


def test_manual_entries_are_valid():
    """записи в MANUAL указывают на существующие сценарии и существующие протоколы"""
    known = archived_scenarios() | {path for paths in change_scenarios().values() for path in paths}
    unknown = sorted(set(MANUAL) - known)
    assert not unknown, "MANUAL ссылается на несуществующие сценарии:\n  " + "\n  ".join(unknown)

    missing = sorted(ref for ref in MANUAL.values() if not (REPO_ROOT / ref).exists())
    assert not missing, "протоколы ручной проверки не найдены:\n  " + "\n  ".join(missing)


def test_no_scenario_is_covered_twice_by_manual_and_test():
    """Ручная расписка на сценарий, который уже покрыт тестом, — забытый мусор."""
    both = sorted(marked() & set(MANUAL))
    assert not both, "покрыты и тестом, и MANUAL — убери из MANUAL:\n  " + "\n  ".join(both)


def test_archived_scenarios_are_covered():
    """Гейт. Сценарий в `openspec/specs/` — постоянное требование системы.

    Красный здесь означает: change заархивирован с непроверяемым требованием.
    До архивации непокрытые сценарии — нормальное «ещё не сделано», их видно в
    отчёте ниже.
    """
    uncovered = sorted(_tracked(archived_scenarios()) - covered())
    assert not uncovered, (
        f"{len(uncovered)} сценариев в постоянных спеках без проверки:\n  " + "\n  ".join(uncovered)
    )


def test_report_change_coverage(capsys):
    """Не гейт, а отчёт: печатает покрытие каждого активного change'а."""
    done = covered()
    lines: list[str] = []

    for change, scenarios in sorted(change_scenarios().items()):
        tracked = _tracked(scenarios)
        if not tracked:
            continue
        lines.append(f"\n{change} — покрытие сценариев: {len(tracked & done)}/{len(tracked)}")

        by_capability: dict[str, set[str]] = defaultdict(set)
        for path in tracked:
            by_capability[path.split("/", 1)[0]].add(path)

        for capability, paths in sorted(by_capability.items()):
            lines.append(f"  {capability}  {len(paths & done)}/{len(paths)}")
            for path in sorted(paths):
                mark = "MANUAL" if path in MANUAL else ("+" if path in done else "-")
                requirement, scenario = path.split("/")[1:]
                lines.append(f"    {mark:>6}  {scenario}   ({requirement})")

    with capsys.disabled():
        print("\n".join(lines))
