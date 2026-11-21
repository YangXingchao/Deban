from . import config


MESSAGES = {
    "en": {
        "port_in_use": "Port %s is already in use. If an earlier instance was suspended with Ctrl+Z, run jobs in that terminal and stop it with kill %%1, or use pkill -f main.py.",
        "prompt_language": "Follow the instructions in English. Preserve the requested output language and JSON keys.",
    },
    "zh": {
        "port_in_use": "端口 %s 已被占用。如果之前的实例被 Ctrl+Z 挂起，请在原终端运行 jobs 并使用 kill %%1 结束，或使用 pkill -f main.py。",
        "prompt_language": "请使用中文理解并执行下列指令，严格遵守指定的输出语言和 JSON 字段。",
    },
    "zh-Hant": {
        "port_in_use": "連接埠 %s 已被佔用。如果先前的程序被 Ctrl+Z 暫停，請在原終端機執行 jobs 並使用 kill %%1 結束，或使用 pkill -f main.py。",
        "prompt_language": "請使用繁體中文理解並執行下列指示，嚴格遵守指定的輸出語言和 JSON 欄位。",
    },
    "ja": {
        "port_in_use": "ポート %s は使用中です。以前のプロセスを Ctrl+Z で停止した場合は、その端末で jobs を実行し kill %%1 で終了するか、pkill -f main.py を使用してください。",
        "prompt_language": "以下の指示を日本語で理解して実行し、指定された出力言語と JSON キーを厳守してください。",
    },
}


def language():
    return config.get("ui_lang") or config._system_lang() or "en"


def message(key):
    return MESSAGES.get(language(), MESSAGES["en"])[key]


def prompt(instructions):
    return f"{message('prompt_language')}\n\n{instructions}"

PROMPTS = {
    "translation": {
        "zh": '你是专业会议译员。结合上下文与术语表，修正明确的语音识别错误，不确定时保留原文。自然地翻译为{out}并保留专有名词。只输出 JSON：{{"src": "修正后的原文", "out": "译文"}}。',
        "zh-Hant": '你是專業會議譯員。結合上下文與術語表，修正明確的語音辨識錯誤，不確定時保留原文。自然地翻譯為{out}並保留專有名詞。只輸出 JSON：{{"src": "修正後的原文", "out": "譯文"}}。',
        "ja": 'あなたは会議の通訳者です。文脈と用語集を使い、明らかな音声認識の誤りを原文の言語で修正し、不確かな場合は原文を維持してください。固有名詞を保持し、{out}に自然に翻訳してください。JSON のみを返してください：{{"src": "修正後の原文", "out": "翻訳"}}。',
    },
    "summary": {
        "zh": '总结会议片段，每句带说话人和 #编号。会议语言为{src}，标题和要点用{out}。只输出 JSON：{{"merge_with_prev": true或false, "title": "简短标题", "points": [{{"text": "要点", "uid": 支撑句编号}}], "fixes": [{{"id": 编号, "text": "修正后的原文"}}], "noise": [编号]}}。仅当片段明显延续前一话题时合并并覆盖两者，否则只总结本段。给出1至5个要点，适当标明说话人。uid 取自字幕或前一话题，不确定则为 null。只修正明确的识别错误，只将明显的短幻觉句列入 noise，保留正常回应。没有修改或噪音时使用空数组。',
        "zh-Hant": '總結會議片段，每句帶有說話者和 #編號。會議語言為{src}，標題和要點使用{out}。只輸出 JSON：{{"merge_with_prev": true或false, "title": "簡短標題", "points": [{{"text": "要點", "uid": 支持句編號}}], "fixes": [{{"id": 編號, "text": "修正後的原文"}}], "noise": [編號]}}。僅當片段明顯延續前一話題時合併並涵蓋兩者，否則只總結本段。給出1至5個要點，適當標明說話者。uid 取自字幕或前一話題，不確定則為 null。只修正明確的辨識錯誤，只將明顯的短幻覺句列入 noise，保留正常回應。沒有修改或雜訊時使用空陣列。',
        "ja": '話者名と #番号 が付いた会議の発言を要約してください。会議言語は{src}、タイトルと要点は{out}です。JSON のみを返してください：{{"merge_with_prev": trueまたはfalse, "title": "短いタイトル", "points": [{{"text": "要点", "uid": 根拠の発言番号}}], "fixes": [{{"id": 発言番号, "text": "修正した原文"}}], "noise": [発言番号]}}。明らかに前の話題の続きである場合のみ両方を統合し、それ以外は今回の部分だけを要約します。要点は1〜5件とし、必要に応じて話者を示します。uid は字幕か前の話題の番号を使い、不明なら null。明らかな認識誤りだけを修正し、明らかな短い幻聴だけを noise に入れ、通常の応答は残します。該当しない場合は空配列を使います。',
    },
    "final": {
        "zh": '根据话题和带编号的完整字幕，用{out}总结会议。只输出 JSON：{{"summary": [{{"text": "要点", "uid": 编号}}], "decisions": [{{"text": "明确决定", "uid": 编号}}], "todos": [{{"text": "行动和期限", "owner": "me、说话人名或空", "uid": 编号}}]}}。summary 给出3至8条结论或分歧。decisions 和 todos 只包含明确提出的项目，没有则为空数组。负责人本人写 me，其他人使用字幕中的名字，不明确则留空。uid 必须来自字幕。',
        "zh-Hant": '根據話題和帶編號的完整字幕，以{out}總結會議。只輸出 JSON：{{"summary": [{{"text": "要點", "uid": 編號}}], "decisions": [{{"text": "明確決定", "uid": 編號}}], "todos": [{{"text": "行動和期限", "owner": "me、說話者姓名或空", "uid": 編號}}]}}。summary 提供3至8條結論或分歧。decisions 和 todos 只包含明確提出的項目，沒有則為空陣列。使用者本人填 me，其他人使用字幕中的姓名，不明確則留空。uid 必須來自字幕。',
        "ja": '話題と番号付きの全文字幕を基に会議を{out}で要約してください。JSON のみを返してください：{{"summary": [{{"text": "要点", "uid": 番号}}], "decisions": [{{"text": "明確な決定", "uid": 番号}}], "todos": [{{"text": "行動と期限", "owner": "me、話者名または空", "uid": 番号}}]}}。summary は結論や相違点を3〜8件。decisions と todos は明示された項目のみとし、なければ空配列。本人は me、他の担当者は字幕内の名前、不明なら空欄。uid は字幕内の番号を使ってください。',
    },
    "refine": {
        "zh": '按时间顺序整理话题，仅合并相邻且明显相同的事项，删除没有信息量的片段。用{out}重写简洁标题和要点。只输出 JSON：{{"topics": [{{"ids": [原话题编号], "title": "标题", "points": [{{"text": "要点", "uid": 支撑句编号或null}}]}}]}}。保持时间顺序和大多数独立话题，删除项的编号不可出现，尽量保留引用编号。',
        "zh-Hant": '按時間順序整理話題，僅合併相鄰且明顯相同的事項，刪除沒有資訊量的片段。用{out}重寫簡潔標題和要點。只輸出 JSON：{{"topics": [{{"ids": [原話題編號], "title": "標題", "points": [{{"text": "要點", "uid": 支持句編號或null}}]}}]}}。保持時間順序和大多數獨立話題，刪除項的編號不可出現，盡量保留引用編號。',
        "ja": '時系列の話題を整理してください。隣接し明らかに同じ内容の部分のみ統合し、情報のない部分は削除します。簡潔なタイトルと要点を{out}で書き直し、JSON のみを返してください：{{"topics": [{{"ids": [元の話題番号], "title": "タイトル", "points": [{{"text": "要点", "uid": 根拠の発言番号またはnull}}]}}]}}。時系列を保ち、大半の話題は独立させ、削除した番号は含めず、根拠番号を可能な限り維持してください。',
    },
    "memory": {
        "zh": '仅从用户本人带编号的发言中提取两类事实：commit 是具体承诺，own 是职责范围。不要推测或将他人的事项归给用户。会议语言为{src}，text 使用{src}。会议日期为{date}，能够确定时将相对期限转换为 YYYY-MM-DD。只输出 JSON：{{"facts": [{{"kind": "commit或own", "text": "事实", "due": "日期或空", "uid": 来源句编号}}]}}；没有则输出 {{"facts": []}}。',
        "zh-Hant": '僅從使用者本人帶編號的發言中擷取兩類事實：commit 是具體承諾，own 是職責範圍。不要推測或將他人的事項歸給使用者。會議語言為{src}，text 使用{src}。會議日期為{date}，能確定時將相對期限轉換為 YYYY-MM-DD。只輸出 JSON：{{"facts": [{{"kind": "commit或own", "text": "事實", "due": "日期或空", "uid": 來源句編號}}]}}；沒有則輸出 {{"facts": []}}。',
        "ja": '利用者本人の番号付き発言から、具体的な約束を表す commit と担当範囲を表す own だけを抽出してください。推測せず、他人の事項を本人に帰属させないでください。会議言語は{src}で、text は{src}で記述します。会議日は{date}です。相対期限は可能な場合 YYYY-MM-DD に変換します。JSON のみを返してください：{{"facts": [{{"kind": "commitまたはown", "text": "事実", "due": "日付または空", "uid": 根拠の発言番号}}]}}。該当しなければ {{"facts": []}}。',
    },
    "opportunity": {
        "zh": '你是 Deban 会议助手。会议语言为{src}，所有输出文本使用{src}。结合用户资料、上下文和材料，只输出 JSON，字段为 type、confidence、urgency、by、question、context_points、stance、situation、reply_guide、reply_src、why、sources。type 为 RESPOND、CONSIDER、CONTRIBUTE、ASK 或 NONE；urgency 为 high 或 normal；stance 为 positive、neutral 或 negative；situation 为 agenda、report、problem、question 或 other。context_points 最多3条；reply_guide 为1至2条回复指导；reply_src 为不超过两句的礼貌口语示例；sources 包含 materials 和 utterances 数组，只列实际依据。RESPOND 表示明确点名并等待回应；CONSIDER 表示可能在询问用户或涉及其职责；CONTRIBUTE 表示用户可主动补充具体有价值的信息；ASK 表示正在征求问题且存在具体未决点；无关或没有价值时为 NONE。符合自定义提醒条件至少为 CONSIDER，明确要求回应时为 RESPOND，并在 question 开头注明条件。考虑语音识别错误。',
        "zh-Hant": '你是 Deban 會議助手。會議語言為{src}，所有輸出文字使用{src}。結合使用者資料、上下文和材料，只輸出 JSON，欄位為 type、confidence、urgency、by、question、context_points、stance、situation、reply_guide、reply_src、why、sources。type 為 RESPOND、CONSIDER、CONTRIBUTE、ASK 或 NONE；urgency 為 high 或 normal；stance 為 positive、neutral 或 negative；situation 為 agenda、report、problem、question 或 other。context_points 最多3條；reply_guide 為1至2條回覆指引；reply_src 為不超過兩句的禮貌口語範例；sources 包含 materials 和 utterances 陣列，只列實際依據。RESPOND 表示明確點名並等待回應；CONSIDER 表示可能在詢問使用者或涉及其職責；CONTRIBUTE 表示使用者可主動補充具體有價值的資訊；ASK 表示正在徵求問題且存在具體未決事項；無關或沒有價值時為 NONE。符合自訂提醒條件至少為 CONSIDER，明確要求回應時為 RESPOND，並在 question 開頭註明條件。考慮語音辨識錯誤。',
        "ja": 'あなたは Deban の会議アシスタントです。会議言語は{src}で、すべての出力文を{src}で書きます。利用者のプロフィール、文脈、資料を基に判断し、type、confidence、urgency、by、question、context_points、stance、situation、reply_guide、reply_src、why、sources を含む JSON のみを返してください。type は RESPOND、CONSIDER、CONTRIBUTE、ASK、NONE のいずれか。urgency は high または normal、stance は positive、neutral、negative、situation は agenda、report、problem、question、other。context_points は最大3件、reply_guide は1〜2点、reply_src は2文以内の丁寧な口語例。sources は materials と utterances の配列で、実際の根拠のみを列挙。RESPOND は明確に指名され応答を待たれている場合、CONSIDER は本人への質問や担当事項に関する場合、CONTRIBUTE は具体的に有益な補足ができる場合、ASK は質問を募っていて具体的な未解決点がある場合、無関係または価値がなければ NONE。カスタム通知条件に該当すれば少なくとも CONSIDER、応答を明示的に求めるなら RESPOND とし、question の冒頭に条件を記載。音声認識の誤りも考慮してください。',
    },
}


def localized_prompt(key, english, **values):
    template = PROMPTS.get(key, {}).get(language())
    if template is None and key == "opportunity":
        return english
    return (template or english).format(**values)
