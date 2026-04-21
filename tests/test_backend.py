"""
PhysicsScholar 后端集成测试脚本
覆盖链路：
  1. 健康检查
  2. 论文上传 → confirm → list → delete
  3. 基础对话流程（ask → get tree → 验证结构）
  4. 重发流程（regenerate → get tree → 验证 status=regenerated）
  5. 点赞/踩
  6. 删除对话

用法：
  pip install requests
  python test_backend.py

  # 如果要跑论文上传，把 PDF_PATH 改成本地实际路径
  python test_backend.py --with-paper
"""

import requests
import sys
import json
import argparse
from pathlib import Path

BASE = "http://localhost:8000"
USER_ID = "test_user"

# ── 颜色输出 ──────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg):
    print(f"  {RED}✗{RESET} {msg}")


def info(msg):
    print(f"  {YELLOW}→{RESET} {msg}")


def section(title):
    print(f"\n{BOLD}{CYAN}{'─' * 50}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 50}{RESET}")


def dump(data):
    print(f"    {json.dumps(data, ensure_ascii=False, indent=2)[:300]}")


# ── 断言工具 ──────────────────────────────────────────────

failed_count = 0


def assert_ok(res, label):
    global failed_count
    if res.status_code in (200, 201):
        ok(f"{label} [{res.status_code}]")
        return True
    else:
        fail(f"{label} [{res.status_code}]")
        try:
            dump(res.json())
        except Exception:
            print(f"    (响应体为空或非 JSON: {res.text[:200]})")
        failed_count += 1
        return False


def assert_field(data, field, label):
    global failed_count
    if field in data and data[field] is not None:
        ok(f"{label}: {data[field]}")
        return True
    else:
        fail(f"{label}：字段 '{field}' 缺失或为 None")
        failed_count += 1
        return False


# ── 链路 1：健康检查 ──────────────────────────────────────


def test_health():
    section("链路 1 · 健康检查")
    res = requests.get(f"{BASE}/health")
    assert_ok(res, "GET /health")
    dump(res.json())


# ── 链路 2：论文管理 ──────────────────────────────────────


def test_paper(pdf_path: str):
    section("链路 2 · 论文管理")
    doc_id = None

    # 2-1 上传
    info(f"上传论文: {pdf_path}")
    with open(pdf_path, "rb") as f:
        res = requests.post(
            f"{BASE}/upload",
            files={"file": (Path(pdf_path).name, f, "application/pdf")},
            data={"user_id": USER_ID, "strict": "false"},
        )
    if not assert_ok(res, "POST /upload"):
        return None
    data = res.json()
    dump(data)
    doc_id = data.get("doc_id")
    title = data.get("title", "测试论文")
    assert_field(data, "doc_id", "doc_id")
    assert_field(data, "status", "status（应为 pending）")

    # 2-2 confirm
    info(f"确认入库: doc_id={doc_id}, title={title}")
    res = requests.post(
        f"{BASE}/confirm",
        json={"doc_id": doc_id, "confirmed_title": title, "user_id": USER_ID},
    )
    assert_ok(res, "POST /confirm")
    dump(res.json())

    # 2-3 list
    res = requests.get(f"{BASE}/papers", params={"user_id": USER_ID})
    assert_ok(res, "GET /papers")
    data = res.json()
    found = any(p["doc_id"] == doc_id for p in data.get("papers", []))
    if found:
        ok(f"论文出现在列表中 (共 {data['count']} 篇)")
    else:
        fail("论文未出现在列表中")

    # 2-4 delete
    res = requests.delete(f"{BASE}/papers/{doc_id}", params={"user_id": USER_ID})
    assert_ok(res, f"DELETE /papers/{doc_id}")

    # 确认已删除
    res = requests.get(f"{BASE}/papers", params={"user_id": USER_ID})
    data = res.json()
    still_there = any(p["doc_id"] == doc_id for p in data.get("papers", []))
    if not still_there:
        ok("删除后论文不再出现在列表中")
    else:
        fail("删除后论文仍出现在列表中")

    return doc_id


# ── 链路 3：基础对话 ──────────────────────────────────────


def test_basic_chat():
    section("链路 3 · 基础对话流程")

    # 3-1 新建对话
    res = requests.post(f"{BASE}/conv_id/new")
    assert_ok(res, "POST /conv_id/new")
    conv_id = res.json().get("conv_id")
    assert_field(res.json(), "conv_id", "conv_id")
    conversation_id = f"{USER_ID}_{conv_id}"
    info(f"conversation_id = {conversation_id}")

    # 3-2 第一条消息（parent_id=None）
    res = requests.post(
        f"{BASE}/ask",
        json={
            "question": "什么是微波光子学？请简要介绍。",
            "conv_id": conv_id,
            "user_id": USER_ID,
            "translation": False,
            "mode": "normal",
            "parent_id": None,
        },
    )
    assert_ok(res, "POST /ask（第一条消息）")
    data = res.json()
    dump(data)
    user_msg_id_1 = data.get("user_msg_id")
    agent_msg_id_1 = data.get("agent_msg_id")
    assert_field(data, "user_msg_id", "user_msg_id")
    assert_field(data, "agent_msg_id", "agent_msg_id")
    assert_field(data, "answer", "answer（前50字）")

    # 3-3 第二条消息（带 parent_id）
    res = requests.post(
        f"{BASE}/ask",
        json={
            "question": "它的主要应用场景有哪些？",
            "conv_id": conv_id,
            "user_id": USER_ID,
            "translation": False,
            "mode": "normal",
            "parent_id": agent_msg_id_1,
        },
    )
    assert_ok(res, "POST /ask（第二条消息，带 parent_id）")
    data = res.json()
    dump(data)
    agent_msg_id_2 = data.get("agent_msg_id")
    assert_field(data, "agent_msg_id", "agent_msg_id")

    # 3-4 get tree 验证结构
    res = requests.get(f"{BASE}/conversation/{conversation_id}/tree")
    assert_ok(res, "GET /conversation/{id}/tree")
    messages = res.json().get("messages", [])
    info(f"树中共 {len(messages)} 条消息")
    if len(messages) >= 4:
        ok("消息数量符合预期（≥4条）")
    else:
        fail(f"消息数量不符合预期，实际 {len(messages)} 条")

    # 验证 parent_id 链路
    ids = {m["id"]: m for m in messages}
    if agent_msg_id_2 and agent_msg_id_2 in ids:
        msg = ids[agent_msg_id_2]
        parent = msg.get("parent_id")
        info(f"第二条 agent 消息的 parent_id = {parent}")
        if parent:
            ok("parent_id 链路正常")
        else:
            fail("parent_id 为空，链路断裂")

    return conv_id, conv_id, agent_msg_id_1, user_msg_id_1


# ── 链路 4：重发 ──────────────────────────────────────────


def test_regenerate(conv_id, user_msg_id, old_agent_msg_id):
    section("链路 4 · 重发流程")
    conversation_id = f"{USER_ID}_{conv_id}"

    if not old_agent_msg_id or not user_msg_id:
        info("缺少必要 id，跳过重发测试")
        return

    info(f"重发：old_agent_msg_id={old_agent_msg_id}, parent_id={user_msg_id}")
    res = requests.post(
        f"{BASE}/regenerate",
        json={
            "question": "什么是微波光子学？请简要介绍。",
            "conv_id": conv_id,
            "user_id": USER_ID,
            "translation": False,
            "mode": "normal",
            "parent_id": user_msg_id,
            "old_agent_msg_id": old_agent_msg_id,
        },
    )
    assert_ok(res, "POST /regenerate")
    data = res.json()
    dump(data)
    new_agent_msg_id = data.get("agent_msg_id")
    assert_field(data, "agent_msg_id", "新 agent_msg_id")

    # 验证旧消息 status=regenerated
    res = requests.get(f"{BASE}/conversation/{conversation_id}/tree")
    assert_ok(res, "GET /conversation/{id}/tree（重发后）")
    messages = res.json().get("messages", [])
    ids = {m["id"]: m for m in messages}

    if old_agent_msg_id in ids:
        old_status = ids[old_agent_msg_id].get("status")
        if old_status == "regenerated":
            ok(f"旧消息 status 已更新为 regenerated")
        else:
            fail(f"旧消息 status = {old_status}，期望 regenerated")
    else:
        fail(f"tree 中找不到旧消息 id={old_agent_msg_id}")

    if new_agent_msg_id and new_agent_msg_id in ids:
        new_version = ids[new_agent_msg_id].get("version")
        info(f"新消息 version = {new_version}（期望 ≥ 2）")
        if new_version and new_version >= 2:
            ok("version 递增正常")
        else:
            fail("version 未递增")

    return new_agent_msg_id


# ── 链路 5：点赞踩 ────────────────────────────────────────


def test_like(agent_msg_id):
    section("链路 5 · 点赞/踩")

    if not agent_msg_id:
        info("缺少 agent_msg_id，跳过")
        return

    for liked, label in [(1, "点赞"), (-1, "点踩"), (0, "取消")]:
        res = requests.patch(
            f"{BASE}/message/{agent_msg_id}/like",
            params={"liked": liked},
        )
        assert_ok(res, f"PATCH /message/{agent_msg_id}/like (liked={liked} {label})")


# ── 链路 6：删除对话 ──────────────────────────────────────


def test_delete_conversation(conv_id):
    section("链路 6 · 删除对话")
    conversation_id = f"{USER_ID}_{conv_id}"

    res = requests.delete(f"{BASE}/conversation/{conversation_id}")
    assert_ok(res, f"DELETE /conversation/{conversation_id}")

    # 验证 tree 为空
    res = requests.get(f"{BASE}/conversation/{conversation_id}/tree")
    assert_ok(res, "GET /conversation/{id}/tree（删除后）")
    messages = res.json().get("messages", [])
    if len(messages) == 0:
        ok("删除后 tree 为空")
    else:
        fail(f"删除后 tree 仍有 {len(messages)} 条消息")


# ── 入口 ──────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--with-paper", metavar="PDF_PATH", help="指定本地 PDF 路径，跑论文管理链路"
    )
    args = parser.parse_args()

    print(f"\n{BOLD}PhysicsScholar 后端集成测试{RESET}")
    print(f"Base URL: {BASE}  User: {USER_ID}")

    test_health()

    if args.with_paper:
        test_paper(args.with_paper)
    else:
        section("链路 2 · 论文管理")
        info("跳过（加 --with-paper <PDF路径> 参数来测试）")

    result = test_basic_chat()
    if result:
        conv_id, _, agent_msg_id_1, user_msg_id_1 = result
        new_agent_id = test_regenerate(conv_id, user_msg_id_1, agent_msg_id_1)
        test_like(new_agent_id or agent_msg_id_1)
        test_delete_conversation(conv_id)

    # 汇总
    print(f"\n{BOLD}{'─' * 50}{RESET}")
    if failed_count == 0:
        print(f"{GREEN}{BOLD}  全部通过 ✓{RESET}")
    else:
        print(f"{RED}{BOLD}  失败 {failed_count} 项 ✗{RESET}")
    print()
    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()
