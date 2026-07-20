from nautilus_openalgo_bridge.risk_state import (
    clear_trading_halt,
    halt_reason,
    is_trading_halted,
    set_trading_halt,
    should_skip_intent,
)


def test_halt_and_clear():
    clear_trading_halt()
    assert not is_trading_halted("aa_1")
    set_trading_halt("aa_1", "max loss")
    assert is_trading_halted("aa_1")
    assert halt_reason("aa_1") == "max loss"
    clear_trading_halt("aa_1")
    assert not is_trading_halted("aa_1")


def test_intent_dedupe():
    from nautilus_openalgo_bridge.risk_state import clear_intent_dedupe

    clear_trading_halt()
    clear_intent_dedupe("aa_1")
    assert should_skip_intent("aa_1", "intent_a") is False
    assert should_skip_intent("aa_1", "intent_a") is True
    assert should_skip_intent("aa_1", "intent_b") is False
