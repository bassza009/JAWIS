import asyncio
import threading

import pytest

from jawis.security.confirmation import ConfirmationManager, TargetProcess


def test_voice_confirmation_parser_handles_negation_and_interrogatives() -> None:
    assert ConfirmationManager.parse_voice_confirmation("ยืนยันครับ") == "approve"
    assert ConfirmationManager.parse_voice_confirmation("ยืนยันค่ะ") == "approve"
    assert ConfirmationManager.parse_voice_confirmation("Confirm") == "approve"
    assert ConfirmationManager.parse_voice_confirmation("ปิดเลย") == "approve"
    assert ConfirmationManager.parse_voice_confirmation("ปิดเลยครับ") == "approve"
    assert ConfirmationManager.parse_voice_confirmation("ตกลงค่ะ") == "approve"
    assert ConfirmationManager.parse_voice_confirmation("โอเคเลย") == "approve"
    assert ConfirmationManager.parse_voice_confirmation("ได้เลยครับ") == "approve"
    assert ConfirmationManager.parse_voice_confirmation("Yes, proceed") == "approve"
    assert ConfirmationManager.parse_voice_confirmation("ยกเลิกครับ") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("No, keep it") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("ไม่") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("ไม่ครับ") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("ไม่ confirm") == "cancel"
    # Colloquial Thai interrogatives & false-positive traps that MUST NOT approve!
    assert ConfirmationManager.parse_voice_confirmation("") is None
    assert ConfirmationManager.parse_voice_confirmation("ใช่ไหม") is None
    assert ConfirmationManager.parse_voice_confirmation("ยืนยันการปิด Chrome ไหมครับ") is None
    assert ConfirmationManager.parse_voice_confirmation("ยืนยันหรือไม่") is None
    assert ConfirmationManager.parse_voice_confirmation("ยืนยันมั้ย") is None
    assert ConfirmationManager.parse_voice_confirmation("ยืนยันมั๊ย") is None
    assert ConfirmationManager.parse_voice_confirmation("ใช่หรอ") is None
    assert ConfirmationManager.parse_voice_confirmation("ยืนยันหรอ") is None
    assert ConfirmationManager.parse_voice_confirmation("ใช่เปล่า") is None
    assert ConfirmationManager.parse_voice_confirmation("โอเคเปล่า") is None
    assert ConfirmationManager.parse_voice_confirmation("ใช่ป่ะ") is None
    assert ConfirmationManager.parse_voice_confirmation("Confirm?") is None
    # Trailing Thai negations/hesitations starting with approval words MUST NOT approve!
    assert ConfirmationManager.parse_voice_confirmation("โอเค งั้นไม่") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("ยืนยันว่าอย่าทำ") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("ตกลงยังไม่อยากปิด") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("อย่ายืนยัน") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("ไม่ยืนยัน") == "cancel"
    # English negations and refusals MUST cancel!
    assert ConfirmationManager.parse_voice_confirmation("don't confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("don’t confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("do not confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("not confirmed") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("won't confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("won’t confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("cannot confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("can't confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("never confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("didn't confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("shouldn't confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("wouldn't confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("couldn't confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("I refuse to confirm") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("decline") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("deny") == "cancel"
    assert ConfirmationManager.parse_voice_confirmation("unconfirmed") is None
    assert ConfirmationManager.parse_voice_confirmation("yesterday") is None
    assert ConfirmationManager.parse_voice_confirmation("I know") is None
    # English substring trap: "now" / "know" must NOT cancel "Confirm now"
    assert ConfirmationManager.parse_voice_confirmation("Confirm now") == "approve"


@pytest.mark.asyncio
async def test_confirmation_approve_and_timeout() -> None:
    mgr = ConfirmationManager(timeout_seconds=0.15)
    req = mgr.create_request(
        action="close_app",
        app_id=1,
        app_key="chrome",
        display_name="Google Chrome",
        target_processes=[TargetProcess(pid=1234, create_time=100.0, process_name="chrome.exe")],
    )
    assert mgr.active_request() == req

    async def approve_soon() -> None:
        await asyncio.sleep(0.03)
        assert mgr.resolve(req.confirm_id, "approve", source="hud") is True
        assert mgr.resolve(req.confirm_id, "approve", source="hud") is False

    asyncio.create_task(approve_soon())
    result = await mgr.wait_for_decision(req.confirm_id)
    assert result.decision == "approve"
    assert result.source == "hud"
    assert mgr.active_request() is None

    req2 = mgr.create_request(
        action="close_app",
        app_id=1,
        app_key="chrome",
        display_name="Google Chrome",
        target_processes=[TargetProcess(pid=1234, create_time=100.0, process_name="chrome.exe")],
    )
    expired = await mgr.wait_for_decision(req2.confirm_id)
    assert expired.decision == "timeout"
    assert expired.source == "timer"
    assert mgr.active_request() is None


@pytest.mark.asyncio
async def test_confirmation_supersede_active_expiry_and_missing() -> None:
    mgr = ConfirmationManager(timeout_seconds=0.2)
    req1 = mgr.create_request(
        action="close_app",
        app_id=1,
        app_key="chrome",
        display_name="Google Chrome",
        target_processes=[TargetProcess(pid=101, create_time=10.0, process_name="chrome.exe")],
    )
    waiter1 = asyncio.create_task(mgr.wait_for_decision(req1.confirm_id))
    await asyncio.sleep(0)

    req2 = mgr.create_request(
        action="close_app",
        app_id=2,
        app_key="notepad",
        display_name="Notepad",
        target_processes=[TargetProcess(pid=202, create_time=20.0, process_name="notepad.exe")],
    )
    res1 = await waiter1
    assert res1.decision == "cancel"
    assert res1.source == "superseded"
    assert mgr.active_request() == req2

    assert mgr.resolve(req2.confirm_id, "cancel", source="voice") is True
    res2 = await mgr.wait_for_decision(req2.confirm_id)
    assert res2.decision == "cancel"
    assert res2.source == "voice"
    assert (await mgr.wait_for_decision(req2.confirm_id)).source == "missing"

    missing = await mgr.wait_for_decision("does-not-exist")
    assert missing.decision == "cancel"
    assert missing.source == "missing"

    short_mgr = ConfirmationManager(timeout_seconds=0.02)
    req3 = short_mgr.create_request(
        action="close_app",
        app_id=3,
        app_key="calc",
        display_name="Calculator",
        target_processes=[],
    )
    await asyncio.sleep(0.05)
    assert short_mgr.active_request() is None
    res3 = await short_mgr.wait_for_decision(req3.confirm_id)
    assert res3.decision == "timeout"
    assert res3.source == "timer"
    assert (await short_mgr.wait_for_decision(req3.confirm_id)).source == "missing"

    req4 = short_mgr.create_request(
        action="close_app",
        app_id=4,
        app_key="calc",
        display_name="Calculator",
        target_processes=[],
    )
    await asyncio.sleep(0.05)
    assert short_mgr.resolve(req4.confirm_id, "approve", source="hud") is False
    res4 = await short_mgr.wait_for_decision(req4.confirm_id)
    assert res4.decision == "timeout"
    assert res4.source == "timer"


@pytest.mark.asyncio
async def test_confirmation_cross_thread_resolve_wakes_loop_cleanly() -> None:
    loop = asyncio.get_running_loop()
    prev_debug = loop.get_debug()
    loop.set_debug(True)
    try:
        mgr = ConfirmationManager(timeout_seconds=1.5)
        req = mgr.create_request(
            action="close_app",
            app_id=5,
            app_key="chrome",
            display_name="Google Chrome",
            target_processes=[TargetProcess(pid=999, create_time=50.0, process_name="chrome.exe")],
        )
        assert isinstance(req.target_processes, tuple)

        resolve_outcomes: list[bool] = []

        def worker() -> None:
            threading.Event().wait(0.03)
            resolve_outcomes.append(mgr.resolve(req.confirm_id, "approve", source="hud"))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        result = await mgr.wait_for_decision(req.confirm_id)
        thread.join(timeout=1.0)

        assert resolve_outcomes == [True]
        assert result.decision == "approve"
        assert result.source == "hud"

        # Immediate expired check inside wait_for_decision (without active_request pre-pruning)
        tiny_mgr = ConfirmationManager(timeout_seconds=0.02)
        req_exp = tiny_mgr.create_request(
            action="close_app",
            app_id=6,
            app_key="notepad",
            display_name="Notepad",
            target_processes=[],
        )
        await asyncio.sleep(0.04)
        expired_res = await tiny_mgr.wait_for_decision(req_exp.confirm_id)
        assert expired_res.decision == "timeout"
        assert expired_res.source == "timer"
    finally:
        loop.set_debug(prev_debug)


def test_confirmation_closed_loop_resolve() -> None:
    closed_mgr = ConfirmationManager(timeout_seconds=5.0)
    temp_loop = asyncio.new_event_loop()
    try:
        async def make_req():
            return closed_mgr.create_request(
                action="close_app",
                app_id=7,
                app_key="calc",
                display_name="Calculator",
                target_processes=[],
            )

        closed_req = temp_loop.run_until_complete(make_req())
    finally:
        temp_loop.close()
    assert closed_mgr.resolve(closed_req.confirm_id, "approve", source="hud") is True
