// 多语言支持：中文 / English / 日本語。
// 用法：HTML 元素标 data-i18n="key"（textContent）、data-i18n-placeholder、
// data-i18n-title；JS 里用 t('key', params)。语言偏好存 localStorage。

/* eslint-disable max-len */
const I18N = {
  zh: {
    lang_label: '语言',
    footer_privacy: '所有推理在本机完成，图片不会发送到任何外部服务。',
    app_tagline: '本地推理 · 图片不出机器',
    disclaimer: '⚠️ 免责声明：本工具仅辅助打码，无法保证完全精准，遮挡效果需自行判断与复核；因使用本工具所遇到的任何审核问题，作者概不负责。',
    key_placeholder: '粘贴 data/api_key 中的内容',
    remember: '记住',
    remember_title: '勾选后密钥会保存在本机浏览器里，下次自动填好',
    key_fill: '一键填写',
    key_fill_title: '从 data/api_key 读出密钥自动填入',
    save: '保存',

    controls_title: '处理选项',
    censor_mode: '打码方式',
    mode_blur: '高斯模糊',
    mode_pixel: '马赛克',
    mode_solid: '纯色填充',
    fill_color: '填充颜色',
    ruleset: '打码规则',
    ruleset_pixiv: 'pixiv 投稿规范',
    ruleset_strict: '严格（全部敏感部位）',
    ruleset_hint: 'pixiv 规范：只遮性器官与肛门（阴蒂、阴部、阴茎、肛门），臀部、胸部不打，便于过审。',
    ruleset_hint_strong: '模型分不清"掰开/没掰开"，也分不出睾丸，这类误差请在手动涂抹里修正。',
    sensitivity: '检测灵敏度',
    sens_max: '最高召回 · 模型看到的都遮（推荐）',
    sens_aggressive: '高召回',
    sens_balanced: '标准 · 门槛 0.30，难姿势容易漏',
    sens_strict: '严格 · 少误杀，可能漏',
    sensitivity_hint: '交合、口交这类难姿势的给分普遍只有 0.10~0.30，用「标准」档会被门槛丢掉 —— ',
    sensitivity_hint_strong: '该遮没遮就选「最高召回」。',
    sensitivity_hint_fg: '女性阴部与男性生殖器各有专属门槛（NSFW_FG_THRESHOLD / NSFW_MG_THRESHOLD），可按需调整。',
    shape_mask: '遮罩形状',
    shape_contour: '贴合部位轮廓（推荐）',
    shape_rect: '矩形框',
    shape_hint: '贴合模式用 SAM 分割出部位的真实边界，只对轮廓内打码，不再把整个框涂成一个方块。矩形模式是原来的行为。',
    mask_inset: '遮罩内收',
    mask_inset_hint: '检测框普遍偏松，往里收一圈能显著减少遮到周围的皮肤。15% 适合大多数情况；发现没遮住边缘再调小。',
    padding: '遮罩扩张',
    padding_hint: '以检测框（矩形模式）或分割出的轮廓（贴合模式）为基准向外扩多少像素。0 = 最精准，不额外扩张。',
    feather: '边缘羽化',
    feather_hint: '轮廓边缘的过渡宽度。0 = 硬边，数值越大越柔和。',
    intensity: '遮挡强度',
    intensity_hint: '高斯模糊用半径，马赛克用块大小。数值越大遮挡越彻底。',
    status_ready: '就绪',
    reset_settings: '恢复默认设置',
    settings_hint: '所有参数都会自动记住，下次打开保持上次的设置。',

    upload_title: '上传图片',
    dropzone_aria: '点击或拖拽图片到此处上传',
    dropzone_title: '点击选择图片，或把图片 / 整个文件夹拖到这里',
    dropzone_hint: '支持 JPG / PNG / WEBP，单张最大 10 MB。拖入文件夹时会自动处理其中全部图片，结果输出到 output/ 下的同名文件夹',

    queue_title: '处理队列',
    files_unit: 'Files',
    pending: 'Pending',
    processing: 'Processing',
    tag_censored: '已打码',
    tag_clean: '干净',
    tag_failed: '失败',

    results_title: '处理结果',
    result_none: '未检出',
    result_img_caption: '打码结果',
    result_img_alt: '打码后的图片',
    decisions_title: '检测明细',
    decisions_empty_hint: '模型检出的每一个部位及其处置结果。若某个位置该遮没遮，这里会写明原因（比如「分数低于门槛」），据此调整上方灵敏度即可。',
    decisions_none: '模型没有在该图中检出任何敏感部位。若你确信图中存在，说明是模型本身漏了 —— 可试试调高灵敏度，或换用更大的检测模型。',
    scores_title: '分类器评分',
    scores_empty: '分类器未返回评分（可能未安装 torch/transformers，仅检测器生效）。',
    download: '下载打码结果',
    start_over: '清空重来',

    editor_title: '手动调整遮罩',
    editor_hint: '自动分割靠颜色边界工作，当部位与周围皮肤颜色接近时它会找不到边界。这里可以直接涂出要遮的范围 —— ',
    editor_hint_strong: '涂哪里就只打哪里',
    editor_hint_tail: '，完全不受模型判断影响。红色区域会被打码。',
    tool_paint: '涂抹',
    tool_erase: '擦除',
    brush: '笔刷',
    undo: '撤回',
    redo: '恢复',
    reset_auto: '恢复自动遮罩',
    clear_all: '全部清除',
    apply_remask: '按此遮罩重新打码',
    editor_idle: '上传图片后即可在此手动调整遮罩。',
    editor_loading: '正在载入原图…',
    editor_ready: '红色区域将被打码。涂抹添加，擦除去掉。',
    editor_after_auto: '已恢复为自动遮罩。红色区域将被打码。',
    editor_cleared: '已清空遮罩。',
    editor_applying: '正在按新遮罩重新打码…',
    editor_no_original: '该结果没有可编辑的原图。',
    editor_mask_fail: '自动遮罩载入失败，已清空。',

    key_cleared: '密钥已清空',
    key_saved_next: '密钥已保存，下次打开自动填好',
    key_saved_session: '密钥已保存到本次会话',
    key_remembered: '已记住密钥，下次打开自动填好',
    key_unremembered: '不再记住密钥，仅本次会话有效',
    key_filled: '已自动填入本机密钥',
    key_fill_fail: '一键填写失败：',
    settings_reset_ok: '已恢复默认设置',
    need_key: '请先在右上角填入 API Key',
    remask_ok: '重新打码完成',
    remask_fail: '重新打码失败',
    remask_fail_with: '重新打码失败：',
    drop_no_images: '拖入的内容里没有支持的图片（JPG / PNG / WEBP）',
    too_many_files: '单次最多处理 {n} 张，已截取前 {n} 张',
    processing_n: '正在处理 {i}/{total}：{name}',
    rate_limit_retry: '触发限流，{sec} 秒后重试（第 {attempt} 次）',
    batch_done: '处理完成，共 {ok}/{total} 张成功',
    analysis_done: '分析完成',
    result_img_fail: '结果图片加载失败，下载令牌可能已过期，请重新处理',
    folder_stats: '文件夹共 {all} 个文件，其中 {imgs} 张图片将被处理',
    error_403: 'API Key 无效或缺失（403）',
    error_413: '文件过大（413）',
    error_429: '请求过于频繁，请稍后再试（429）',

    summary_detected: '共识别 {d} 处目标，实际打码 {b} 处。',
    summary_none: '未打码。',
    summary_skipped: ' 另有 {n} 处敏感部位被判定为无需遮挡，详见「检测明细」；若其中有该遮住的，把检测灵敏度调高。',
    summary_contour: ' 轮廓贴合已生效：遮罩面积约为检测框的 {pct}%。',
    shape_contour_short: '轮廓',
    shape_rect_short: '矩形',

    reason_merged: '相邻部位已合并为同一遮罩区域',
    reason_bridged_face: '已向邻近面部延伸以覆盖接触区域',
    reason_censor_plain: '已打码',
    reason_censor_contour: '已打码（轮廓贴合）',
    reason_censor_rect: '已打码（矩形）',
    reason_score_below: '分数 {score} 低于{level}门槛 {th}',
    reason_high_risk_below: '高风险图，但分数 {score} 低于 {th}',
    reason_normal_skip: '整图判定为正常（{score}），该部位属有歧义区域，按防误杀策略跳过',
    reason_normal_loose: '整图倾向正常（{score}），分数 {score2} 未达 {th}',
    reason_ambiguous_below: '分数 {score} 低于 {th}',
    reason_out_of_scope: '当前规则（{rule}）不需要遮挡该部位',
    reason_invalid_box: '检测框无效',
    reason_empty_region: '遮罩区域为空',
    reason_fg_threshold: '分数 {score} 低于女性阴部专用门槛 {th}（一线天不打码）',
    reason_fg_skipped: '暴露区域过小（疑似一线天），未打码',
    level_max: '最高召回',
    level_aggressive: '高召回',
    level_balanced: '标准',
    level_strict: '严格',
    ruleset_name_pixiv: 'pixiv 投稿规范',
    ruleset_name_strict: '严格',

    label_FACE_FEMALE: '女性面部',
    label_FACE_MALE: '男性面部',
    label_FEMALE_GENITALIA_EXPOSED: '女性阴部（暴露）',
    label_FEMALE_GENITALIA_COVERED: '女性阴部（被遮挡）',
    label_MALE_GENITALIA_EXPOSED: '男性生殖器（暴露）',
    label_MALE_GENITALIA_COVERED: '男性生殖器（被遮挡）',
    label_FEMALE_BREAST_EXPOSED: '女性乳房（暴露）',
    label_FEMALE_BREAST_COVERED: '女性乳房（被遮挡）',
    label_BUTTOCKS_EXPOSED: '臀部（暴露）',
    label_BUTTOCKS_COVERED: '臀部（被遮挡）',
    label_ANUS_EXPOSED: '肛门（暴露）',
    label_ANUS_COVERED: '肛门（被遮挡）',
    label_BELLY_EXPOSED: '腹部（暴露）',
    label_BELLY_COVERED: '腹部（被遮挡）',
    label_FEET_EXPOSED: '足部（暴露）',
    label_FEET_COVERED: '足部（被遮挡）',
    label_ARMPITS_EXPOSED: '腋下（暴露）',
    label_ARMPITS_COVERED: '腋下（被遮挡）',
  },

  en: {
    lang_label: 'Language',
    footer_privacy: 'All inference runs locally; images are never sent to any external service.',
    app_tagline: 'Local inference · images never leave your machine',
    disclaimer: '⚠️ Disclaimer: This tool only assists with masking and may not be fully accurate — always review the results yourself. The author is not responsible for any content-review issues arising from its use.',
    key_placeholder: 'Paste the content of data/api_key',
    remember: 'Remember',
    remember_title: 'The key will be stored in this browser and filled in automatically next time',
    key_fill: 'Auto-fill',
    key_fill_title: 'Read the key from data/api_key and fill it in',
    save: 'Save',

    controls_title: 'Options',
    censor_mode: 'Censoring method',
    mode_blur: 'Gaussian blur',
    mode_pixel: 'Mosaic',
    mode_solid: 'Solid fill',
    fill_color: 'Fill color',
    ruleset: 'Censoring rules',
    ruleset_pixiv: 'pixiv submission rules',
    ruleset_strict: 'Strict (all sensitive parts)',
    ruleset_hint: 'pixiv rules: only genitals and anus get censored; buttocks and chest stay visible.',
    ruleset_hint_strong: 'The model cannot tell "spread open" from "closed", nor testicles — fix those by manual painting.',
    sensitivity: 'Detection sensitivity',
    sens_max: 'Max recall · censor everything detected (recommended)',
    sens_aggressive: 'High recall',
    sens_balanced: 'Standard · threshold 0.30, hard poses may slip',
    sens_strict: 'Strict · fewer false hits, may miss',
    sensitivity_hint: 'Hard poses (intercourse, oral) usually score only 0.10–0.30 and get dropped by the "Standard" threshold — ',
    sensitivity_hint_strong: 'if something should be censored but is not, choose "Max recall".',
    sensitivity_hint_fg: 'Female genitalia and male genitalia each have their own threshold (NSFW_FG_THRESHOLD / NSFW_MG_THRESHOLD).',
    shape_mask: 'Mask shape',
    shape_contour: 'Follow body contour (recommended)',
    shape_rect: 'Rectangle',
    shape_hint: 'Contour mode uses SAM to segment the real boundary of the part and censors only inside it. Rectangle is the old behavior.',
    mask_inset: 'Mask inset',
    mask_inset_hint: 'Detection boxes run loose; shrinking inward greatly reduces skin caught by the mask. 15% fits most cases; lower it if edges get missed.',
    padding: 'Mask expand',
    padding_hint: 'How many pixels to expand beyond the box (rect mode) or contour (contour mode). 0 = most precise.',
    feather: 'Edge feather',
    feather_hint: 'Transition width at mask edges. 0 = hard edge; higher is softer.',
    intensity: 'Intensity',
    intensity_hint: 'Blur radius for Gaussian, block size for mosaic. Higher = stronger.',
    status_ready: 'Ready',
    reset_settings: 'Reset to defaults',
    settings_hint: 'All settings are remembered automatically and restored next time.',

    upload_title: 'Upload images',
    dropzone_aria: 'Click or drop images here to upload',
    dropzone_title: 'Click to pick images, or drag images / a whole folder here',
    dropzone_hint: 'JPG / PNG / WEBP, up to 10 MB each. Drop a folder to process every image inside; results go into a same-named folder under output/',

    queue_title: 'Queue',
    files_unit: 'Files',
    pending: 'Pending',
    processing: 'Processing',
    tag_censored: 'Censored',
    tag_clean: 'Clean',
    tag_failed: 'Failed',

    results_title: 'Results',
    result_none: 'Nothing detected',
    result_img_caption: 'Censored result',
    result_img_alt: 'Censored image',
    decisions_title: 'Detection details',
    decisions_empty_hint: 'Every detected part and how it was handled. If something should be censored but is not, the reason is shown here.',
    decisions_none: 'No sensitive parts detected in this image. If you are sure there are some, the model itself missed them — try higher sensitivity or a larger detection model.',
    scores_title: 'Classifier scores',
    scores_empty: 'No classifier scores (torch/transformers may be missing; detector only).',
    download: 'Download censored image',
    start_over: 'Start over',

    editor_title: 'Manual mask editor',
    editor_hint: 'Automatic segmentation works on color boundaries and fails when the part looks like the surrounding skin. Paint the area to censor here — ',
    editor_hint_strong: 'you paint it, only that gets censored',
    editor_hint_tail: ', regardless of the model. Red areas will be censored.',
    tool_paint: 'Paint',
    tool_erase: 'Erase',
    brush: 'Brush',
    undo: 'Undo',
    redo: 'Redo',
    reset_auto: 'Auto mask',
    clear_all: 'Clear all',
    apply_remask: 'Re-censor with this mask',
    editor_idle: 'Upload an image to edit its mask here.',
    editor_loading: 'Loading original…',
    editor_ready: 'Red areas will be censored. Paint to add, erase to remove.',
    editor_after_auto: 'Auto mask restored. Red areas will be censored.',
    editor_cleared: 'Mask cleared.',
    editor_applying: 'Re-censoring with the new mask…',
    editor_no_original: 'No editable original for this result.',
    editor_mask_fail: 'Failed to load the auto mask; cleared.',

    key_cleared: 'Key cleared',
    key_saved_next: 'Key saved; it will be filled in automatically next time',
    key_saved_session: 'Key saved for this session',
    key_remembered: 'Key remembered; it will be filled in automatically next time',
    key_unremembered: 'Key no longer remembered; valid for this session only',
    key_filled: 'Local key filled in',
    key_fill_fail: 'Auto-fill failed: ',
    settings_reset_ok: 'Settings restored to defaults',
    need_key: 'Enter the API Key in the top-right corner first',
    remask_ok: 'Re-censoring done',
    remask_fail: 'Re-censoring failed',
    remask_fail_with: 'Re-censoring failed: ',
    drop_no_images: 'No supported images (JPG / PNG / WEBP) in what you dropped',
    too_many_files: 'At most {n} files per run; keeping the first {n}',
    processing_n: 'Processing {i}/{total}: {name}',
    rate_limit_retry: 'Rate limited; retrying in {sec} s (attempt {attempt})',
    batch_done: 'Done: {ok}/{total} succeeded',
    analysis_done: 'Analysis complete',
    result_img_fail: 'Failed to load the result image; the download token may have expired. Process again.',
    folder_stats: '{all} files in the folder; {imgs} images will be processed',
    error_403: 'API key invalid or missing (403)',
    error_413: 'File too large (413)',
    error_429: 'Too many requests, slow down (429)',

    summary_detected: '{d} detections, {b} censored.',
    summary_none: 'Nothing censored.',
    summary_skipped: ' {n} in-scope parts were skipped — see "Detection details"; raise the sensitivity if any of them should be censored.',
    summary_contour: ' Contour masking active: mask area is about {pct}% of the box.',
    shape_contour_short: 'Contour',
    shape_rect_short: 'Rect',

    reason_merged: 'adjacent parts merged into one mask region',
    reason_bridged_face: 'extended toward the nearby face to cover the contact area',
    reason_censor_plain: 'Censored',
    reason_censor_contour: 'Censored (body contour)',
    reason_censor_rect: 'Censored (rectangle)',
    reason_score_below: 'score {score} below {level} threshold {th}',
    reason_high_risk_below: 'high-risk image, but score {score} below {th}',
    reason_normal_skip: 'image judged normal ({score}); ambiguous area skipped by the false-positive guard',
    reason_normal_loose: 'image leans normal ({score}); score {score2} below {th}',
    reason_ambiguous_below: 'score {score} below {th}',
    reason_out_of_scope: 'not in scope under the current rule ({rule})',
    reason_invalid_box: 'invalid detection box',
    reason_empty_region: 'empty mask region',
    reason_fg_threshold: 'score {score} below the female-genitalia threshold {th} (closed "line" shapes are not censored)',
    reason_fg_skipped: 'Exposed area too small (likely a closed slit); not censored',
    level_max: 'max recall',
    level_aggressive: 'high recall',
    level_balanced: 'standard',
    level_strict: 'strict',
    ruleset_name_pixiv: 'pixiv rules',
    ruleset_name_strict: 'strict',

    label_FACE_FEMALE: 'Female face',
    label_FACE_MALE: 'Male face',
    label_FEMALE_GENITALIA_EXPOSED: 'Female genitalia (exposed)',
    label_FEMALE_GENITALIA_COVERED: 'Female genitalia (covered)',
    label_MALE_GENITALIA_EXPOSED: 'Male genitalia (exposed)',
    label_MALE_GENITALIA_COVERED: 'Male genitalia (covered)',
    label_FEMALE_BREAST_EXPOSED: 'Female breast (exposed)',
    label_FEMALE_BREAST_COVERED: 'Female breast (covered)',
    label_BUTTOCKS_EXPOSED: 'Buttocks (exposed)',
    label_BUTTOCKS_COVERED: 'Buttocks (covered)',
    label_ANUS_EXPOSED: 'Anus (exposed)',
    label_ANUS_COVERED: 'Anus (covered)',
    label_BELLY_EXPOSED: 'Belly (exposed)',
    label_BELLY_COVERED: 'Belly (covered)',
    label_FEET_EXPOSED: 'Feet (exposed)',
    label_FEET_COVERED: 'Feet (covered)',
    label_ARMPITS_EXPOSED: 'Armpits (exposed)',
    label_ARMPITS_COVERED: 'Armpits (covered)',
  },

  ja: {
    lang_label: '言語',
    footer_privacy: 'すべての推論はローカルで行われ、画像は外部サービスに送信されません。',
    app_tagline: 'ローカル推論・画像は端末外に出ません',
    disclaimer: '⚠️ 免責事項：本ツールはモザイク処理の補助であり、必ずしも正確ではありません。処理結果はご自身で判断・確認してください。本ツールの使用に関わる審査上の問題について、作者は一切の責任を負いません。',
    key_placeholder: 'data/api_key の内容を貼り付け',
    remember: '記憶',
    remember_title: 'チェックするとキーがブラウザに保存され、次回自動入力されます',
    key_fill: '自動入力',
    key_fill_title: 'data/api_key からキーを読み込んで入力します',
    save: '保存',

    controls_title: '処理オプション',
    censor_mode: 'マスキング方式',
    mode_blur: 'ガウスぼかし',
    mode_pixel: 'モザイク',
    mode_solid: '単色塗りつぶし',
    fill_color: '塗りつぶし色',
    ruleset: 'マスキング規則',
    ruleset_pixiv: 'pixiv 投稿ルール',
    ruleset_strict: '厳格（全有感部位）',
    ruleset_hint: 'pixiv ルール：性器と肛門のみマスキングし、臀部や胸部はそのまま残します。',
    ruleset_hint_strong: 'モデルは「開いている/閉じている」や睾丸を判別できません。誤差は手動塗りで修正してください。',
    sensitivity: '検出感度',
    sens_max: '最高再現率・検出したすべてをマスキング（推奨）',
    sens_aggressive: '高再現率',
    sens_balanced: '標準・しきい値 0.30、難しいポーズは見逃しやすい',
    sens_strict: '厳格・誤検出は少ないが見逃す可能性',
    sensitivity_hint: '性行為・オーラルなど難しいポーズのスコアは 0.10〜0.30 にとどまることが多く、「標準」では除外されます —— ',
    sensitivity_hint_strong: 'マスキングされるべきものが漏れたら「最高再現率」を選んでください。',
    sensitivity_hint_fg: '女性器・男性器それぞれ専用しきい値があります（NSFW_FG_THRESHOLD / NSFW_MG_THRESHOLD）。',
    shape_mask: 'マスク形状',
    shape_contour: '部位の輪郭に沿う（推奨）',
    shape_rect: '矩形',
    shape_hint: '輪郭モードは SAM で部位の実際の境界を分割し、輪郭内のみマスキングします。矩形は従来の動作です。',
    mask_inset: 'マスク内側縮小',
    mask_inset_hint: '検出枠は一般に緩めです。内側に縮めると周囲の肌への誤マスクが大幅に減ります。15% が目安で、端が漏れたら下げてください。',
    padding: 'マスク拡張',
    padding_hint: '検出枠（矩形）または輪郭（輪郭モード）から外側に何ピクセル広げるか。0 = 最も正確。',
    feather: 'エッジぼかし',
    feather_hint: 'マスク端のなじみ幅。0 = ハードエッジ、大きいほど柔らかい。',
    intensity: '強度',
    intensity_hint: 'ガウスぼかしは半径、モザイクはブロック size。大きいほど強く。',
    status_ready: 'スタンバイ',
    reset_settings: '既定値に戻す',
    settings_hint: '設定はすべて自動保存され、次回も保持されます。',

    upload_title: '画像をアップロード',
    dropzone_aria: 'クリックまたはドラッグで画像をアップロード',
    dropzone_title: 'クリックして選択、または画像 / フォルダをここにドロップ',
    dropzone_hint: 'JPG / PNG / WEBP、1枚 10MB まで。フォルダをドロップすると中の全画像を処理し、結果は output/ 下の同名フォルダに出力されます',

    queue_title: '処理キュー',
    files_unit: '件',
    pending: '待機中',
    processing: '処理中',
    tag_censored: 'マスキング済み',
    tag_clean: '問題なし',
    tag_failed: '失敗',

    results_title: '処理結果',
    result_none: '検出なし',
    result_img_caption: 'マスキング結果',
    result_img_alt: 'マスキング後の画像',
    decisions_title: '検出詳細',
    decisions_empty_hint: '検出された各部位とその処理結果。マスキングされるべきものが漏れた場合、ここに理由が表示されます。',
    decisions_none: 'この画像から敏感部位は検出されませんでした。確実に存在する場合はモデルの見逃しです —— 感度を上げるか、より大きな検出モデルをお試しください。',
    scores_title: '分類スコア',
    scores_empty: '分類スコアがありません（torch/transformers 未導入の可能性。検出のみ有効）。',
    download: 'マスキング結果をダウンロード',
    start_over: 'クリア',

    editor_title: '手動マスク編集',
    editor_hint: '自動分割は色の境界に依存するため、部位と周囲の肌の色が近いと境界を見つけられません。ここに直接塗ってください —— ',
    editor_hint_strong: '塗ったところだけがマスキングされます',
    editor_hint_tail: '。モデルの判定に影響されません。赤い部分がマスキングされます。',
    tool_paint: '塗る',
    tool_erase: '消しゴム',
    brush: 'ブラシ',
    undo: '元に戻す',
    redo: 'やり直す',
    reset_auto: '自動マスクに戻す',
    clear_all: 'すべて消去',
    apply_remask: 'このマスクで再マスキング',
    editor_idle: '画像をアップロードするとここでマスクを編集できます。',
    editor_loading: '原图を読み込み中…',
    editor_ready: '赤い部分がマスキングされます。塗って追加、消して除去。',
    editor_after_auto: '自動マスクを復元しました。赤い部分がマスキングされます。',
    editor_cleared: 'マスクを消去しました。',
    editor_applying: '新しいマスクで再マスキング中…',
    editor_no_original: 'この結果には編集可能な原图がありません。',
    editor_mask_fail: '自動マスクの読み込みに失敗しました。消去しました。',

    key_cleared: 'キーを消去しました',
    key_saved_next: 'キーを保存しました。次回自動入力されます',
    key_saved_session: 'キーをこのセッションに保存しました',
    key_remembered: 'キーを記憶しました。次回自動入力されます',
    key_unremembered: 'キーを記憶しません。このセッションのみ有効',
    key_filled: 'ローカルキーを入力しました',
    key_fill_fail: '自動入力に失敗：',
    settings_reset_ok: '既定値に戻しました',
    need_key: 'まず右上に API キーを入力してください',
    remask_ok: '再マスキング完了',
    remask_fail: '再マスキングに失敗しました',
    remask_fail_with: '再マスキングに失敗：',
    drop_no_images: 'ドロップされた内容に対応する画像（JPG / PNG / WEBP）がありません',
    too_many_files: '1回あたり最大 {n} 枚です。先頭の {n} 枚を処理します',
    processing_n: '処理中 {i}/{total}：{name}',
    rate_limit_retry: 'レート制限。{sec} 秒後に再試行します（{attempt} 回目）',
    batch_done: '完了：{ok}/{total} 枚成功',
    analysis_done: '解析完了',
    result_img_fail: '結果画像の読み込みに失敗しました。トークンの期限切れの可能性。再処理してください。',
    folder_stats: 'フォルダ内 {all} ファイルのうち、{imgs} 枚の画像を処理します',
    error_403: 'API キーが無効または未入力（403）',
    error_413: 'ファイルが大きすぎます（413）',
    error_429: 'リクエストが多すぎます。しばらくお待ちください（429）',

    summary_detected: '{d} 件検出、{b} 件マスキング。',
    summary_none: 'マスキングなし。',
    summary_skipped: ' 他 {n} 件はマスキング不要と判定されました。詳細は「検出詳細」を参照。マスキングされるべきものがあれば感度を上げてください。',
    summary_contour: ' 輪郭マスキング有効：マスク面積は検出枠の約 {pct}%。',
    shape_contour_short: '輪郭',
    shape_rect_short: '矩形',

    reason_merged: '隣接部位を同一マスク領域に統合',
    reason_bridged_face: '近くの顔まで範囲を延長して接触領域をカバー',
    reason_censor_plain: 'マスキング済み',
    reason_censor_contour: 'マスキング済み（輪郭）',
    reason_censor_rect: 'マスキング済み（矩形）',
    reason_score_below: 'スコア {score} が{level}しきい値 {th} 未満',
    reason_high_risk_below: '高リスク画像ですが、スコア {score} が {th} 未満',
    reason_normal_skip: '画像は正常と判定（{score}）。曖昧な領域は誤検出防止によりスキップ',
    reason_normal_loose: '画像はやや正常（{score}）。スコア {score2} が {th} 未満',
    reason_ambiguous_below: 'スコア {score} が {th} 未満',
    reason_out_of_scope: '現在のルール（{rule}）では対象外',
    reason_invalid_box: '無効な検出枠',
    reason_empty_region: 'マスク領域が空',
    reason_fg_threshold: 'スコア {score} が女性器の専用しきい値 {th} 未満（閉じた状態はマスキングしない）',
    reason_fg_skipped: '露出領域が小さすぎる（閉じた状態の可能性）；マスキングなし',
    level_max: '最高再現率',
    level_aggressive: '高再現率',
    level_balanced: '標準',
    level_strict: '厳格',
    ruleset_name_pixiv: 'pixiv ルール',
    ruleset_name_strict: '厳格',

    label_FACE_FEMALE: '女性の顔',
    label_FACE_MALE: '男性の顔',
    label_FEMALE_GENITALIA_EXPOSED: '女性器（露出）',
    label_FEMALE_GENITALIA_COVERED: '女性器（遮蔽）',
    label_MALE_GENITALIA_EXPOSED: '男性器（露出）',
    label_MALE_GENITALIA_COVERED: '男性器（遮蔽）',
    label_FEMALE_BREAST_EXPOSED: '女性の胸（露出）',
    label_FEMALE_BREAST_COVERED: '女性の胸（遮蔽）',
    label_BUTTOCKS_EXPOSED: '臀部（露出）',
    label_BUTTOCKS_COVERED: '臀部（遮蔽）',
    label_ANUS_EXPOSED: '肛門（露出）',
    label_ANUS_COVERED: '肛門（遮蔽）',
    label_BELLY_EXPOSED: '腹部（露出）',
    label_BELLY_COVERED: '腹部（遮蔽）',
    label_FEET_EXPOSED: '足（露出）',
    label_FEET_COVERED: '足（遮蔽）',
    label_ARMPITS_EXPOSED: '脇（露出）',
    label_ARMPITS_COVERED: '脇（遮蔽）',
  },
};

const LANG_KEY = 'nsfw_lang';

function detectLang() {
  const langs = (navigator.languages || [navigator.language || 'en']).join(',').toLowerCase();
  if (langs.startsWith('zh')) return 'zh';
  if (langs.startsWith('ja')) return 'ja';
  return 'en';
}

let currentLang = localStorage.getItem(LANG_KEY) || detectLang();
if (!I18N[currentLang]) currentLang = 'zh';

function t(key, params) {
  const table = I18N[currentLang] || I18N.zh;
  let text = table[key];
  if (text === undefined) text = I18N.zh[key];
  if (text === undefined) return key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      text = text.split('{' + k + '}').join(String(v));
    }
  }
  return text;
}

function tLabel(label) {
  const key = 'label_' + label;
  const table = I18N[currentLang] || I18N.zh;
  return table[key] || I18N.zh[key] || label;
}

function applyLang() {
  document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : currentLang;
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    el.setAttribute('placeholder', t(el.getAttribute('data-i18n-placeholder')));
  });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => {
    el.setAttribute('title', t(el.getAttribute('data-i18n-title')));
  });
  document.querySelectorAll('[data-i18n-aria]').forEach((el) => {
    el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria')));
  });
  const selector = document.getElementById('langSelect');
  if (selector) selector.value = currentLang;
}

function setLang(lang) {
  if (!I18N[lang]) return;
  currentLang = lang;
  localStorage.setItem(LANG_KEY, lang);
  applyLang();
}

// 后端返回的 reason 是中文句子。按模式识别并翻译，参数（分数/门槛）从
// 原文里提取。识别不了的直接显示原文 —— 总比显示错误的翻译好。
function explainReason(reason) {
  if (!reason) return '';
  let m;
  if (reason.startsWith('已打码')) {
    const merged = reason.includes('相邻部位已合并');
    const bridged = reason.includes('面部');
    const key = reason.includes('轮廓')
      ? 'reason_censor_contour'
      : reason.includes('矩形')
        ? 'reason_censor_rect'
        : 'reason_censor_plain';
    let out = t(key);
    const suffix = [];
    if (merged) suffix.push(t('reason_merged'));
    if (bridged) suffix.push(t('reason_bridged_face'));
    return suffix.length ? out + '；' + suffix.join('；') : out;
  }
  if ((m = reason.match(/分数 ([\d.]+) 低于(.+?)门槛 ([\d.]+)/))) {
    return t('reason_score_below', { score: m[1], level: t('level_' + m[2]) || m[2], th: m[3] });
  }
  if ((m = reason.match(/分数 ([\d.]+) 低于女性阴部专用门槛 ([\d.]+)/))) {
    return t('reason_fg_threshold', { score: m[1], th: m[2] });
  }
  if ((m = reason.match(/高风险图，但分数 ([\d.]+) 低于 ([\d.]+)/))) {
    return t('reason_high_risk_below', { score: m[1], th: m[2] });
  }
  if ((m = reason.match(/整图判定为正常（([\d.]+)）/))) {
    return t('reason_normal_skip', { score: m[1] });
  }
  if ((m = reason.match(/整图倾向正常（([\d.]+)），分数 ([\d.]+) 未达 ([\d.]+)/))) {
    return t('reason_normal_loose', { score: m[1], score2: m[2], th: m[3] });
  }
  if ((m = reason.match(/分数 ([\d.]+) 低于 ([\d.]+)/))) {
    return t('reason_ambiguous_below', { score: m[1], th: m[2] });
  }
  if ((m = reason.match(/当前规则（(.+?)）不需要遮挡该部位/))) {
    const ruleKey = m[1].includes('pixiv') ? 'ruleset_name_pixiv' : 'ruleset_name_strict';
    return t('reason_out_of_scope', { rule: t(ruleKey) });
  }
  if (reason.includes('一线天') && reason.includes('暴露区域')) return t('reason_fg_skipped');
  if (reason.includes('检测框')) return t('reason_invalid_box');
  if (reason.includes('遮罩区域为空')) return t('reason_empty_region');
  return reason;
}
