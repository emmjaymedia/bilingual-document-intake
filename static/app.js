const el = id => document.getElementById(id);
const fields = ["invoice_number","subtotal","tax","total"];
let jobId;
async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the form and try again.");
  return data;
}
async function run(form, action) {
  if (document.querySelector("form button:disabled")) return;
  const buttons = [...form.querySelectorAll("button")];
  buttons.forEach(button => {button.disabled = true;});
  try { await action(); } catch (error) { el("status").textContent = error.message; }
  finally { buttons.forEach(button => {button.disabled = false;}); }
}
async function refreshQueue() {
  const jobs = await request("/api/documents");
  el("jobs").replaceChildren(...jobs.map(job => new Option(
    job.id.slice(0,8)+" · "+job.language.toUpperCase()+" · "+job.status, job.id,
  )));
  el("load-job").disabled = !jobs.length;
}
async function waitForReview() {
  for (let attempt = 0; attempt < 60; attempt++) {
    const current = await request("/api/documents/"+jobId);
    if (current.status === "failed") throw new Error(current.error);
    if (current.status === "review") {
      for (const key of fields) el(key).value = current.fields[key] ?? "";
      el("ocr").textContent = current.ocr_text ?? "";
      el("status").textContent = current.error ?? "Fields ready for review.";
      el("review").hidden = false;
      await refreshQueue();
      return;
    }
    el("status").textContent = current.status === "queued" ? "Queued for OCR…" : "Reading invoice…";
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  el("status").textContent = "Processing continues. Job ID: "+jobId;
}
el("load-job").onclick = () => run(el("intake"), async () => {
  jobId = el("jobs").value;
  el("review").hidden = true;
  el("ocr").textContent = "";
  await waitForReview();
});
refreshQueue().catch(error => {el("status").textContent = error.message;});
el("intake").onsubmit = event => {
  event.preventDefault();
  run(event.target, async () => {
    el("review").hidden = true;
    el("ocr").textContent = "";
    el("status").textContent = "Preparing image…";
    const language = el("language").value;
    let image = el("image").files[0];
    if (event.submitter?.value === "sample") {
      const sample = await fetch("/samples/"+language);
      if (!sample.ok) throw new Error("Sample unavailable.");
      image = await sample.blob();
    }
    if (!image) throw new Error("Choose a PNG or JPEG image.");
    const form = new FormData();
    form.set("image", image, "invoice");
    const job = await request("/api/documents?language="+language, {method:"POST", body:form});
    jobId = job.id;
    await waitForReview();
  });
};
el("review").onsubmit = event => {
  event.preventDefault();
  run(event.target, async () => {
    await request("/api/documents/"+jobId+"/review", {
      method:"PUT", headers:{"Content-Type":"application/json"},
      body:JSON.stringify(Object.fromEntries(fields.map(key => [key,el(key).value]))),
    });
    el("review").hidden = true;
    el("status").textContent = "Invoice approved.";
    await refreshQueue();
  });
};
