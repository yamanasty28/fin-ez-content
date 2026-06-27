#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FIN-EZ 毎日記事 自動生成（クラウド版・GitHub Actions 用）

Anthropic Messages API（Claude）＋ web_search サーバーツールで「今日」の実ニュースを
調べ、CLAUDE.md のルールに従って最低7本の初心者向け記事＋辞書用語を生成し、
articles.json に追記する。検証に通った場合のみ書き込み、ワークフロー側で commit & push する。

依存: Python 標準ライブラリのみ（urllib）。SDK 不要。
必要な環境変数: ANTHROPIC_API_KEY
任意: MODEL（既定 claude-opus-4-8）
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("MODEL", "claude-sonnet-4-6")  # 高品質Opus→約4割安のSonnetに切替。Opusに戻すなら "claude-opus-4-8"
API_KEY = os.environ.get("ANTHROPIC_API_KEY")

JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
TODAY = NOW.strftime("%Y-%m-%d")
TODAY_COMPACT = NOW.strftime("%Y%m%d")
PUB = NOW.strftime("%Y-%m-%dT06:00:00+09:00")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLES_PATH = os.path.join(REPO_ROOT, "articles.json")
BUNDLED_PATH = os.path.join(REPO_ROOT, "bundled-terms.json")
RULES_PATH = os.path.join(REPO_ROOT, "CLAUDE.md")

GENRES = ["stocks", "crypto", "fx", "economy", "money"]

NG_WORDS = [
    "買い時", "仕込み", "今が買い", "急騰期待", "爆上がり", "今週来る", "おすすめ銘柄",
    "注目銘柄", "必ず", "絶対", "間違いなく", "安全資産", "将来有望", "AIスコア", "勝率",
    "利益最大化", "今のうちに", "急げ", "見逃すな", "おすすめします", "推奨します", "鉄則",
]


def _headers():
    return {
        "content-type": "application/json",
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
    }


def _post(payload, timeout=600):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, headers=_headers(), method="POST")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 503, 529) and attempt < 3:
                wait = 2 ** attempt * 5
                print(f"  HTTP {e.code}, retry in {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise SystemExit(f"API error {e.code}: {body}")
        except urllib.error.URLError as e:
            if attempt < 3:
                time.sleep(2 ** attempt * 5)
                continue
            raise SystemExit(f"Network error: {e}")
    raise SystemExit("API unreachable")


def _text_from(resp):
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


def research_news():
    """Phase A: web_search で今日の各ジャンルのニュースを調べ、事実だけ箇条書きで返す。"""
    user = (
        f"今日は{TODAY}（日本時間）。FIN-EZという初心者向け金融ニュースアプリ用に、"
        "次の5ジャンルそれぞれについて『今日〜ここ数日』の実際のニュースをweb_searchで調べてください："
        "stocks（株式・日経平均/S&P500等）, crypto（ビットコイン等）, fx（ドル円等）, "
        "economy（FRB/日銀/経済指標）, money（家計・物価・金利の身近な話題）。\n\n"
        "各ジャンル2件以上の独立ソースで事実確認し、確認できた数値（価格・変化率・日付）だけを使うこと。"
        "未確認の数値や将来予測は書かない。最後に、各ジャンルごとに『今日初心者に伝える価値があるトピック』を"
        "1〜3個、日本語の箇条書きで、確認できた数値と出典メディア名を添えてまとめてください。"
        "ニュースが薄いジャンルは、その旨を書き、基礎解説のテーマ案を提示してください。"
    )
    payload = {
        "model": MODEL,
        "max_tokens": 8000,
        "tools": [{"type": "web_search_20260209", "name": "web_search"}],
        "messages": [{"role": "user", "content": user}],
    }
    messages = payload["messages"]
    for _ in range(6):
        resp = _post(payload)
        stop = resp.get("stop_reason")
        if stop == "pause_turn":
            messages.append({"role": "assistant", "content": resp["content"]})
            payload["messages"] = messages
            continue
        return _text_from(resp)
    return _text_from(resp)


SCHEMA_SPEC = """
出力は **JSONのみ**（マークダウンのコードフェンス禁止）。形は:
{
  "articles": [ Article, ... ],   // 最低7本。順番は重要度順（無料3本を先頭に）
  "terms": [ Term, ... ]          // 記事で説明した専門用語のうち、まだ辞書に無いものだけ
}

Article（全フィールド必須。body は空文字 ""）:
{
  "id": "daily-YYYYMMDD-<genre>",  // 同ジャンル2本目以降は -2, -3 と連番
  "genre": "stocks|crypto|fx|economy|money",
  "title": "「！」「？」を使った読みたくなる見出し",
  "summary": "2〜3文。一覧と冒頭に表示",
  "body": "",
  "infographic": [ IGBlock, ... ],
  "readingTimeMinutes": 3-5,
  "xpReward": 12-20,
  "publishedAt": "<PUB>",
  "isPremium": false,
  "tags": ["…","…"]
}

IGBlock の並び（標準構成）:
1 {"kind":"points","title":"30秒でわかる","items":["…","…","…"]}
2 {"kind":"card","num":1,"title":"まずは結論！","body":["…"],"char":{"pose":"fin1..fin8","bubble":"日常の例え"},"pill":{"text":"重要数字","emphasis":"数字"}}
3 {"kind":"card","num":2,"title":"なぜ〜？（原因）","body":["…"]}
4 {"kind":"section","text":"どうつながる？（カンタン図解）"}
5 {"kind":"flow","title":"…","steps":[{"emoji":"🏦","tone":"b|g|p|r|o|y","label":"…","sub":"←なぜ？を必ず"}]}
6 {"kind":"section","text":"初心者向け解説"}
7 {"kind":"qa","items":[{"q":"…","a":"…"}]}  // 2〜3問。「これからどうなる?」には「未来は誰にもわかりません」
8 {"kind":"numbers","title":"数字で見る今回の動き","items":[{"value":"…","label":"…","change":"任意","trend":"up|down|flat"}]}  // 確認できた実数値のみ
9 {"kind":"life","title":"生活でこう役立つ","items":["…","…"]}  // 毎記事必須・読者の生活に結びつける
10 {"kind":"take","text":"今日の学び（1文）"}
11 {"kind":"quiz","question":"…","options":["…","…","…"],"correctIndex":0,"answer":"こたえ：…解説","pose":"fin5"}  // correctIndex必須
12 {"kind":"note","label":"おうちの人へ","text":"…"}  // 任意

Term:
{"id":"term-daily-<slug>","term":"…","reading":"ひらがな(英略語は\\"\\")","simple":"絵文字つき一言例え",
 "category":"stocks|crypto|fx|economy|money|general","shortDefinition":"1〜2文",
 "fullDefinition":"3〜4文の初心者向け","exampleSentence":"「」つき生活が浮かぶ例文",
 "relatedTermIds":[],"difficulty":1,"isPremium":false}
"""


def build_system():
    return (
        "あなたはFIN-EZ（小学生・中学生でも理解できる初心者向け金融ニュースアプリ）の編集者です。"
        "以下のCLAUDE.mdのルール（NGワード・用語の言い換え・やさしいトーン・インフォグラフィック構成・"
        "プレミアム/辞書ルール・法務）に**完全に従い**、本文を段落の羅列にせずインフォグラフィックで作ること。\n\n"
        "=== CLAUDE.md ===\n" + open(RULES_PATH, encoding="utf-8").read()
    )


# 部分一致でも弾かれる禁止語。本文・見出し・要約・タグ・quizの選択肢/解説・例文・辞書定義の
# どこにも絶対に出してはいけない。中立表現へ言い換えること。
NG_BAN = (
    "【最重要・厳守】次の語は、記事の本文・タイトル・要約・タグ・quizの選択肢や解説(answer)・"
    "例文・辞書定義を含め、JSONのどこにも一切使わないこと（部分一致でも検証で弾かれて全体が失敗する）：\n"
    + "、".join(NG_WORDS) + "。\n"
    "投資をすすめる/煽る表現は中立表現に言い換える。例：『買い時』→『〜という動きが見られています（投資判断はご自身で）』、"
    "『今が買い』→使わず削除、『必ず〜』→『〜という傾向があります』『〜とは限りません』、"
    "『絶対』『間違いなく』→使わない、『安全資産』→『比較的値動きが穏やかとされる』。"
    "クイズの誤答選択肢にもこれらの語を入れないこと。\n"
)


def _stream_messages(system, messages, max_tokens=32000):
    """Messages API をストリーミングで叩き、テキストを結合して返す。"""
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "stream": True,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, headers=_headers(), method="POST")
    out = []
    stop_reason = None
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                t = ev.get("type")
                if t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    out.append(ev["delta"]["text"])
                elif t == "message_delta":
                    stop_reason = ev.get("delta", {}).get("stop_reason", stop_reason)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"API error {e.code}: {e.read().decode('utf-8','replace')}")
    text = "".join(out)
    if stop_reason == "max_tokens":
        raise SystemExit("生成が max_tokens で打ち切られました（JSON不完全）。")
    return text


def generate_articles(facts, existing_terms):
    """Phase B: 事実をもとに7本＋辞書をJSONで生成（ストリーミングで受信）。"""
    rules = (
        f"今日は{TODAY}。下の『調査メモ』の確認済み事実だけを使って（数値の捏造・将来予測は禁止）、"
        "本日分の記事を **最低7本** 作る。各ジャンル(stocks/crypto/fx/economy/money)を必ず1本以上カバーし、"
        "ニュースが多い人気ジャンル（株式・仮想通貨・経済が多くなりやすい）を厚くして7本以上にする。"
        "ニュースが薄いジャンルは捏造せず基礎解説記事で1本埋める。\n"
        "**無料はちょうど3本（最重要・なるべく違うジャンル、isPremium:false）、残りは全部プレミアム(isPremium:true)。**\n"
        "外貨表記には円換算（約◯円）を添える。数値には比較・変化・水準を添える。"
        "暗号資産の記事には末尾に必ず note でリスク注記（価格変動が大きく元本割れの恐れ・預金保険対象外・本アプリは売買を行わない）。\n"
        "専門用語で説明が必要なものは、下の『既存辞書』に無ければ terms に追加する。\n\n"
        + NG_BAN + "\n"
        f"publishedAt は全記事 \"{PUB}\" を使う。id の日付は {TODAY_COMPACT}。\n\n"
        + SCHEMA_SPEC.replace("<PUB>", PUB)
        + "\n\n=== 既存辞書（重複追加禁止の用語名） ===\n"
        + ", ".join(sorted(existing_terms))
        + "\n\n=== 調査メモ ===\n" + facts
        + "\n\nそれではJSONのみを出力してください。"
    )
    return _stream_messages(build_system(), [{"role": "user", "content": rules}])


def repair_articles(prior_text, errs):
    """検証エラーをAIに差し戻し、該当箇所だけ直した完成版JSONを再出力させる。"""
    fix = (
        "前回出力したJSONに下記の検証エラーがありました。**該当箇所だけ**を直し、他はできるだけ保ったまま、"
        "修正後の完成版JSONを丸ごと再出力してください（JSONのみ・コードフェンス禁止）。\n\n"
        + NG_BAN
        + "\n=== 検証エラー ===\n- " + "\n- ".join(errs)
        + "\n\n=== 前回のJSON ===\n" + prior_text
        + "\n\n修正後のJSONのみを出力してください。"
    )
    return _stream_messages(build_system(), [{"role": "user", "content": fix}])


def parse_json_block(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise SystemExit("JSONが見つかりません。\n" + text[:500])
    return json.loads(text[start:end + 1])


def validate(payload, bundled, existing_terms):
    arts = payload.get("articles", [])
    errs = []
    if len(arts) < 7:
        errs.append(f"記事が{len(arts)}本（7本以上必要）")
    genres = {a.get("genre") for a in arts}
    if genres != set(GENRES):
        errs.append(f"全5ジャンル未カバー: {sorted(genres)}")
    free = [a for a in arts if not a.get("isPremium")]
    if len(free) != 3:
        errs.append(f"無料が{len(free)}本（ちょうど3本必要）")
    ids = [a.get("id") for a in arts]
    if len(ids) != len(set(ids)):
        errs.append("id重複あり")
    for a in arts:
        kinds = [b.get("kind") for b in a.get("infographic", [])]
        for need in ("points", "life", "quiz", "take"):
            if need not in kinds:
                errs.append(f"{a.get('id')}: {need}ブロック欠落")
        for b in a.get("infographic", []):
            if b.get("kind") == "quiz":
                ci = b.get("correctIndex")
                if not isinstance(ci, int) or not (0 <= ci < len(b.get("options", []))):
                    errs.append(f"{a.get('id')}: quiz correctIndex不正")
                if not b.get("answer"):
                    errs.append(f"{a.get('id')}: quiz answer欠落")
        if a.get("genre") == "crypto":
            if not any(bl.get("kind") == "note" and "リスク" in (bl.get("label", "") + bl.get("text", ""))
                       for bl in a.get("infographic", [])):
                errs.append(f"{a.get('id')}: 暗号資産リスク注記なし")
    blob = json.dumps(payload, ensure_ascii=False)
    hits = [w for w in NG_WORDS if w in blob]
    if hits:
        errs.append(f"NGワード混入: {hits}")
    # 辞書: 既存と重複する新規termは除外（エラーにはしない）
    for t in payload.get("terms", []):
        if t.get("term") in existing_terms:
            t["_dup"] = True
    return errs


def main():
    if not API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY が未設定です。")
    data = json.load(open(ARTICLES_PATH, encoding="utf-8"))
    if any(a.get("id", "").startswith(f"daily-{TODAY_COMPACT}") for a in data["articles"]):
        print(f"{TODAY} の記事は既に存在します。スキップ。")
        return
    bundled = json.load(open(BUNDLED_PATH, encoding="utf-8"))
    bundled_terms = {t["term"] for t in bundled.get("terms", [])}
    feed_terms = {t["term"] for t in data.get("terms", [])}
    existing_terms = bundled_terms | feed_terms

    print("Phase A: ニュース調査（web_search）…", flush=True)
    facts = research_news()
    print(facts[:1500], flush=True)

    print("\nPhase B: 記事生成…", flush=True)
    text = generate_articles(facts, existing_terms)
    out = parse_json_block(text)
    errs = validate(out, bundled, existing_terms)

    # 検証エラーはAIに差し戻して直させる（NGワード等）。最大3回までリトライ。
    for attempt in range(3):
        if not errs:
            break
        print(f"\n検証エラー → 修正を依頼（{attempt + 1}/3）:\n- " + "\n- ".join(errs), flush=True)
        text = repair_articles(text, errs)
        out = parse_json_block(text)
        errs = validate(out, bundled, existing_terms)

    if errs:
        raise SystemExit("検証エラー（修正後も解消せず）:\n- " + "\n- ".join(errs))

    new_arts = out["articles"]
    data["articles"] = new_arts + data["articles"]
    if len(data["articles"]) > 200:
        data["articles"] = data["articles"][:200]
    added = []
    for t in out.get("terms", []):
        if t.get("_dup") or t.get("term") in existing_terms:
            continue
        t.pop("_dup", None)
        data["terms"].append(t)
        added.append(t["term"])
    data["updatedAt"] = PUB

    json.dump(data, open(ARTICLES_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    n_free = sum(1 for a in new_arts if not a.get("isPremium"))
    print(f"\n✅ {len(new_arts)}本追加（無料{n_free}）/ 辞書+{len(added)}語: {added}")
    print("articles total:", len(data["articles"]))
    # ワークフローのコミットメッセージ用
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"count={len(new_arts)}\n")
            f.write(f"date={TODAY}\n")


if __name__ == "__main__":
    main()
