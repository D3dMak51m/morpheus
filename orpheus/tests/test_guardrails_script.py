"""
Тесты Stage 46: инородное письмо, протекающее в комментарий.

qwen2.5:3b выдаёт куски обучающих данных посреди фразы («批评или ошибка»), и такой
комментарий проходил все проверки: ни одна из них не смотрела на алфавит. При этом
комментарий ЦЕЛИКОМ на другом письме — нормальная работа (персона отвечает на языке
поста), поэтому ловим именно вкрапление, а не наличие символов.

    docker compose exec orpheus python -m pytest tests -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.guardrails import OutputGuardrails, _script_bleed       # noqa: E402

GUARDS = OutputGuardrails()


def test_cjk_run_inside_a_russian_comment_is_rejected():
    ok, reason = GUARDS.validate_output("Это 批评或错误 конечно, но метро всё равно лучше")
    assert not ok and "CJK" in reason


def test_arabic_run_inside_a_russian_comment_is_rejected():
    ok, _ = GUARDS.validate_output("Нормально всё, просто نعم надо подождать немного")
    assert not ok


def test_a_comment_written_wholly_in_chinese_passes():
    """Персона отвечает на языке поста — китайский комментарий под китайским постом
    это не утечка, а требуемое поведение."""
    ok, _ = GUARDS.validate_output("这条评论完全是中文写的，没有任何问题存在啊")
    assert ok


def test_latin_inside_russian_is_normal_human_writing():
    for text in ("Купил новый iPhone, ok, дорого но норм работает",
                 "Смотрю на Telegram и понимаю: пробки никуда не делись"):
        ok, reason = GUARDS.validate_output(text)
        assert ok, reason


def test_clean_russian_has_no_bleed():
    assert _script_bleed("Согласен, автобусы бы почаще пускали") == ""
