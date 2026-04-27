const loginPanel = document.getElementById('loginPanel');
const appPanel = document.getElementById('appPanel');
const currentUser = document.getElementById('currentUser');

const loginForm = document.getElementById('loginForm');
const logoutBtn = document.getElementById('logoutBtn');
const createExamForm = document.getElementById('createExamForm');
const seedSamplesBtn = document.getElementById('seedSamplesBtn');
const examSelect = document.getElementById('examSelect');
const examInfo = document.getElementById('examInfo');
const answerKeyForm = document.getElementById('answerKeyForm');
const rubricForm = document.getElementById('rubricForm');
const batchForm = document.getElementById('batchForm');
const batchStatus = document.getElementById('batchStatus');
const refreshResultsBtn = document.getElementById('refreshResultsBtn');
const exportCsvBtn = document.getElementById('exportCsvBtn');
const exportXlsxBtn = document.getElementById('exportXlsxBtn');
const resultsTableBody = document.querySelector('#resultsTable tbody');
const detailPanel = document.getElementById('detailPanel');
const detailContent = document.getElementById('detailContent');
const overrideForm = document.getElementById('overrideForm');
const toast = document.getElementById('toast');
const rubricSteppers = document.querySelectorAll('.stepper');
const erKeywordList = document.getElementById('erKeywordList');
const addErKeywordBtn = document.getElementById('addErKeywordBtn');
const toggleAdvancedRubricBtn = document.getElementById('toggleAdvancedRubricBtn');
const advancedRubricPanel = document.getElementById('advancedRubricPanel');
const presetBalancedBtn = document.getElementById('presetBalancedBtn');
const presetStructureBtn = document.getElementById('presetStructureBtn');

let exams = [];
let selectedExamId = null;

function notify(message, isError = false) {
  toast.textContent = message;
  toast.style.borderColor = isError ? 'rgba(255,107,107,0.5)' : 'rgba(38,196,133,0.5)';
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2200);
}

async function req(url, options = {}) {
  const res = await fetch(url, options);
  const contentType = res.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await res.json() : null;
  if (!res.ok) {
    throw new Error(data?.error || `Request failed: ${res.status}`);
  }
  return data;
}

function getCurrentExamId() {
  if (!selectedExamId) throw new Error('กรุณาเลือกข้อสอบ');
  return selectedExamId;
}

function renderExamSelect() {
  examSelect.innerHTML = '';
  if (exams.length === 0) {
    examSelect.innerHTML = '<option value="">ยังไม่มีข้อสอบ</option>';
    selectedExamId = null;
    examInfo.textContent = 'ยังไม่มีข้อสอบ';
    return;
  }

  exams.forEach((exam) => {
    const opt = document.createElement('option');
    opt.value = exam.id;
    opt.textContent = `#${exam.id} ${exam.title} (${exam.diagram_type})`;
    examSelect.appendChild(opt);
  });

  if (!selectedExamId || !exams.find((x) => x.id === selectedExamId)) {
    selectedExamId = exams[0].id;
  }
  examSelect.value = String(selectedExamId);
  updateExamInfo();
}

function updateExamInfo() {
  const exam = exams.find((e) => e.id === Number(examSelect.value));
  if (!exam) return;
  selectedExamId = exam.id;
  examInfo.textContent = `สถานะ: ${exam.status} | มีเฉลย: ${exam.answer_key_id ? 'Yes' : 'No'} | submissions: ${exam.submission_count}`;
}

async function loadExams() {
  const data = await req('/exams');
  exams = data.items || [];
  renderExamSelect();
  if (selectedExamId) {
    await loadRubric(selectedExamId);
  }
}

function setRubricForm(rubric) {
  if (!rubric) return;
  rubricForm.component_weight.value = Number(rubric.component_weight ?? 40);
  rubricForm.text_weight.value = Number(rubric.text_weight ?? 30);
  rubricForm.structure_weight.value = Number(rubric.structure_weight ?? 30);
  rubricForm.pass_score.value = Number(rubric.pass_score ?? 60);

  if (erKeywordList) {
    erKeywordList.innerHTML = '';
    const erKeywords = Array.isArray(rubric.er_keywords) ? rubric.er_keywords : [];
    erKeywords.forEach((item) => createErKeywordRow(item));
  }
}

function createErKeywordRow(data = {}) {
  if (!erKeywordList) return;

  const row = document.createElement('div');
  row.className = 'keyword-row';
  row.innerHTML = `
    <select class="keyword-topic">
      <option value="entity" ${(data.topic || 'entity') === 'entity' ? 'selected' : ''}>entity</option>
      <option value="relationship" ${data.topic === 'relationship' ? 'selected' : ''}>relationship</option>
      <option value="attribute" ${data.topic === 'attribute' ? 'selected' : ''}>attribute</option>
      <option value="other" ${data.topic === 'other' ? 'selected' : ''}>other</option>
    </select>
    <input type="text" class="keyword-expected" placeholder="คำที่ต้องเจอ เช่น student หรือ enroll" value="${data.expected_text || ''}" />
    <input type="number" class="keyword-points" min="0" step="1" value="${Number(data.points ?? 10)}" />
    <label class="criteria-critical"><input type="checkbox" class="keyword-critical" ${data.critical ? 'checked' : ''} />critical</label>
    <button type="button" class="remove-btn">x</button>
  `;

  row.querySelector('.remove-btn').addEventListener('click', () => {
    row.remove();
  });

  erKeywordList.appendChild(row);
}

function collectErKeywords() {
  if (!erKeywordList) return [];

  const rows = erKeywordList.querySelectorAll('.keyword-row');
  const items = [];
  rows.forEach((row) => {
    const expected_text = row.querySelector('.keyword-expected')?.value?.trim();
    if (!expected_text) return;
    const topic = row.querySelector('.keyword-topic')?.value || 'entity';
    const points = Number(row.querySelector('.keyword-points')?.value || 0);
    const critical = !!row.querySelector('.keyword-critical')?.checked;
    items.push({ topic, expected_text, points, critical });
  });
  return items;
}

async function loadRubric(examId) {
  const data = await req(`/exams/${examId}/rubric`);
  setRubricForm(data.rubric);
}

function scoreCell(value) {
  if (value === null || value === undefined) return '-';
  return Number(value).toFixed(2);
}

async function loadResults() {
  const examId = getCurrentExamId();
  const data = await req(`/exams/${examId}/results`);
  resultsTableBody.innerHTML = '';

  data.items.forEach((item) => {
    const tr = document.createElement('tr');
    const statusClass = item.status === 'done' ? 'done' : item.status === 'failed' ? 'failed' : 'processing';
    tr.innerHTML = `
      <td>${item.id}</td>
      <td>${item.student_id || '-'}</td>
      <td>${item.file_name}</td>
      <td><span class="tag ${statusClass}">${item.status}</span></td>
      <td>${scoreCell(item.score_total)}</td>
      <td>${scoreCell(item.effective_score)}</td>
      <td><button class="btn ghost" data-id="${item.id}">Detail</button></td>
    `;
    resultsTableBody.appendChild(tr);
  });

  resultsTableBody.querySelectorAll('button[data-id]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.getAttribute('data-id');
      await loadDetail(id);
    });
  });
}

async function loadDetail(submissionId) {
  const detail = await req(`/submissions/${submissionId}/result-detail`);
  detailPanel.classList.remove('hidden');

  const feedback = detail.feedback || [];
  const ocrTexts = (detail.ocr_texts || []).map((x) => `${x.text_value} (${Number(x.confidence).toFixed(1)})`);
  const erKeywordResults = detail.result?.er_keyword_results || [];
  const erKeywordLines = erKeywordResults.map((k) => {
    return `${k.topic} | expected: ${k.expected_text} | ${k.matched ? 'matched' : 'missing'} | ${Number(k.earned || 0).toFixed(2)}/${Number(k.points || 0).toFixed(2)} | ${k.note || '-'}`;
  });

  detailContent.innerHTML = `
    <p><b>Submission #${detail.id}</b> | Student: ${detail.student_id || '-'} | File: ${detail.file_name}</p>
    <p>Score: ${scoreCell(detail.score_total)} | Component: ${scoreCell(detail.score_component)} | Text: ${scoreCell(detail.score_text)} | Structure: ${scoreCell(detail.score_structure)}</p>
    <p>OCR Available: ${detail.result?.ocr_available ? 'Yes' : 'No'} | OCR Provider: ${detail.result?.ocr_provider || '-'} | OCR Tokens: ${detail.result?.ocr_token_count ?? 0} | Type Penalty: ${scoreCell(detail.result?.type_penalty ?? 0)}</p>
    <p>ER Keyword Score: ${scoreCell(detail.result?.er_keyword_score ?? 0)} | ER Keyword Critical Failed: ${detail.result?.er_keyword_critical_failed ?? 0}</p>
    <p>Processed at: ${detail.processed_at || '-'}</p>
    <ul class="feedback-list">${feedback.map((f) => `<li>${f}</li>`).join('')}</ul>
    <div class="codebox"><b>OCR Texts</b><br />${ocrTexts.length ? ocrTexts.join('<br />') : 'ไม่พบข้อความ OCR'}</div>
    <div class="codebox"><b>ER Keyword Breakdown</b><br />${erKeywordLines.length ? erKeywordLines.join('<br />') : 'ไม่มีหัวข้อ ER keyword'}</div>
  `;

  overrideForm.submission_id.value = detail.id;
  overrideForm.score.value = detail.overridden_score ?? detail.score_total ?? 0;
}

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const formData = new FormData(loginForm);
  try {
    const data = await req('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: formData.get('username'), password: formData.get('password') }),
    });
    currentUser.textContent = `${data.user.username} (${data.user.role})`;
    loginPanel.classList.add('hidden');
    appPanel.classList.remove('hidden');
    await loadExams();
    if (selectedExamId) await loadResults();
    notify('เข้าสู่ระบบสำเร็จ');
  } catch (err) {
    notify(err.message, true);
  }
});

logoutBtn.addEventListener('click', async () => {
  await req('/auth/logout', { method: 'POST' });
  location.reload();
});

createExamForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const payload = Object.fromEntries(new FormData(createExamForm).entries());
    await req('/exams', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    createExamForm.reset();
    await loadExams();
    await loadResults();
    notify('สร้างข้อสอบแล้ว');
  } catch (err) {
    notify(err.message, true);
  }
});

seedSamplesBtn.addEventListener('click', async () => {
  try {
    const data = await req('/seed/sample-exams', { method: 'POST' });
    await loadExams();
    if (selectedExamId) await loadResults();

    if (data.created_count > 0) {
      notify(`เพิ่มตัวอย่างข้อสอบ ${data.created_count} ชุด (ข้าม ${data.skipped_count})`);
    } else {
      notify('มีตัวอย่างข้อสอบครบแล้ว');
    }
  } catch (err) {
    notify(err.message, true);
  }
});

examSelect.addEventListener('change', async () => {
  updateExamInfo();
  detailPanel.classList.add('hidden');
  if (selectedExamId) await loadRubric(selectedExamId);
  if (selectedExamId) await loadResults();
});

answerKeyForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const examId = getCurrentExamId();
    const fd = new FormData(answerKeyForm);
    const data = await req(`/exams/${examId}/answer-key`, { method: 'POST', body: fd });
    notify(`อัปโหลดเฉลยแล้ว v${data.version}`);
    if (data.rubric) {
      setRubricForm(data.rubric);
      const generated = Number(data.auto_er_keyword_count || 0);
      const source = data.auto_er_keyword_source || 'unknown';
      if (generated > 0 && advancedRubricPanel && toggleAdvancedRubricBtn) {
        advancedRubricPanel.classList.remove('hidden');
        toggleAdvancedRubricBtn.textContent = 'ซ่อนตั้งค่าขั้นสูง';
        if (source === 'ocr') {
          notify(`สร้าง ER Keyword จาก OCR อัตโนมัติ ${generated} รายการ แก้ไขได้ทันที`);
        } else if (source === 'placeholder') {
          notify(`OCR อ่านข้อความไม่ครบ จึงสร้างรายการ placeholder ${generated} รายการให้แก้ไขแทน`);
        } else if (source === 'not-er') {
          notify('ข้อสอบนี้ไม่ใช่ ER จึงไม่สร้าง ER Keyword อัตโนมัติ');
        }
      }
      if (generated === 0) {
        notify('ยังไม่สามารถสร้าง ER Keyword ได้ ลองตรวจ OCR provider/คุณภาพภาพเฉลย', true);
      }
    } else {
      await loadRubric(examId);
    }
    await loadExams();
  } catch (err) {
    notify(err.message, true);
  }
});

rubricSteppers.forEach((btn) => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.target;
    const step = Number(btn.dataset.step || 0);
    const input = rubricForm[target];
    if (!input) return;

    const current = Number(input.value || 0);
    const min = input.min ? Number(input.min) : Number.NEGATIVE_INFINITY;
    const max = input.max ? Number(input.max) : Number.POSITIVE_INFINITY;
    const next = Math.min(max, Math.max(min, current + step));
    input.value = String(next);
  });
});

rubricForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const examId = getCurrentExamId();
    const payload = {
      component_weight: Number(rubricForm.component_weight.value || 40),
      text_weight: Number(rubricForm.text_weight.value || 30),
      structure_weight: Number(rubricForm.structure_weight.value || 30),
      pass_score: Number(rubricForm.pass_score.value || 60),
      er_keywords: collectErKeywords(),
    };
    await req(`/exams/${examId}/rubric`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    notify('บันทึก rubric แล้ว');
  } catch (err) {
    notify(err.message, true);
  }
});

if (addErKeywordBtn) {
  addErKeywordBtn.addEventListener('click', () => {
    createErKeywordRow({ topic: 'entity', expected_text: '', points: 10, critical: false });
  });
}

if (toggleAdvancedRubricBtn && advancedRubricPanel) {
  toggleAdvancedRubricBtn.addEventListener('click', () => {
    const isHidden = advancedRubricPanel.classList.contains('hidden');
    if (isHidden) {
      advancedRubricPanel.classList.remove('hidden');
      toggleAdvancedRubricBtn.textContent = 'ซ่อนตั้งค่าขั้นสูง';
    } else {
      advancedRubricPanel.classList.add('hidden');
      toggleAdvancedRubricBtn.textContent = 'แสดงตั้งค่าขั้นสูง';
    }
  });
}

if (presetBalancedBtn) {
  presetBalancedBtn.addEventListener('click', () => {
    rubricForm.component_weight.value = 35;
    rubricForm.text_weight.value = 30;
    rubricForm.structure_weight.value = 35;
    rubricForm.pass_score.value = 60;
    notify('ตั้งค่า Preset สมดุลแล้ว');
  });
}

if (presetStructureBtn) {
  presetStructureBtn.addEventListener('click', () => {
    rubricForm.component_weight.value = 25;
    rubricForm.text_weight.value = 20;
    rubricForm.structure_weight.value = 55;
    rubricForm.pass_score.value = 70;
    notify('ตั้งค่า Preset เน้นโครงสร้างแล้ว');
  });
}

batchForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const examId = getCurrentExamId();
    const fd = new FormData(batchForm);
    batchStatus.textContent = 'กำลังประมวลผล...';
    const data = await req(`/exams/${examId}/submissions/batch`, { method: 'POST', body: fd });
    const lines = data.items.map((x) => {
      if (x.status === 'done') return `${x.file} -> score ${x.score}`;
      return `${x.file} -> failed (${x.reason || 'error'})`;
    });
    batchStatus.textContent = lines.join('\n') || 'ไม่พบไฟล์ที่ประมวลผลได้';
    await loadExams();
    await loadResults();
    notify('ประมวลผล batch เสร็จแล้ว');
  } catch (err) {
    batchStatus.textContent = err.message;
    notify(err.message, true);
  }
});

refreshResultsBtn.addEventListener('click', async () => {
  try {
    await loadResults();
    notify('อัปเดตผลแล้ว');
  } catch (err) {
    notify(err.message, true);
  }
});

async function exportAndMaybeClear(format) {
  if (!selectedExamId) return;

  const shouldClear = window.confirm('ต้องการ Export และล้างผลตรวจเก่าของข้อสอบนี้หลัง Export หรือไม่?\nกด OK = Export + ล้างผลเก่า\nกด Cancel = Export อย่างเดียว');

  window.open(`/exams/${selectedExamId}/export?format=${format}`, '_blank');

  if (!shouldClear) {
    notify('Export อย่างเดียว (ยังไม่ล้างผลเก่า)');
    return;
  }

  try {
    const data = await req(`/exams/${selectedExamId}/results/clear`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });

    detailPanel.classList.add('hidden');
    await loadExams();
    if (selectedExamId) await loadResults();
    notify(`ล้างผลเก่าแล้ว ${data.cleared || 0} รายการ`);
  } catch (err) {
    notify(err.message, true);
  }
}

exportCsvBtn.addEventListener('click', async () => {
  await exportAndMaybeClear('csv');
});

exportXlsxBtn.addEventListener('click', async () => {
  await exportAndMaybeClear('xlsx');
});

overrideForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const payload = Object.fromEntries(new FormData(overrideForm).entries());
    const submissionId = payload.submission_id;
    await req(`/submissions/${submissionId}/override-score`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ score: payload.score, note: payload.note }),
    });
    await loadResults();
    await loadDetail(submissionId);
    notify('Override สำเร็จ');
  } catch (err) {
    notify(err.message, true);
  }
});

async function boot() {
  try {
    const data = await req('/auth/me');
    if (data.user) {
      currentUser.textContent = `${data.user.username} (${data.user.role})`;
      loginPanel.classList.add('hidden');
      appPanel.classList.remove('hidden');
      await loadExams();
      if (selectedExamId) await loadResults();
    }
  } catch (err) {
    console.error(err);
  }
}

boot();
