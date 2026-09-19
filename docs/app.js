"use strict";

const $ = (id) => document.getElementById(id);
const number = (value) => new Intl.NumberFormat("en").format(value);
const date = (value) => value ? value.replace("T", " ").replace(/\.\d+/, "").replace("+00:00", "Z").replace("Z", " UTC") : "Not available";
let downloadUrl;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function field(label, value) {
  const group = element("div");
  group.append(element("dt", label), element("dd", value ?? "Not available"));
  return group;
}

function renderSnapshot(snapshot) {
  const source = snapshot.source;
  const heading = element("div", undefined, "snapshot-heading");
  heading.append(element("h3", source.resolved_title));
  const pageUrl = safeUrl(snapshot.page?.fullurl);
  if (pageUrl) {
    const link = element("a", "Source page ↗");
    link.href = pageUrl;
    heading.append(link);
  }
  const identity = element("dl", undefined, "identity");
  identity.append(field("Page ID", source.pageid), field("Revision ID", source.revision_id), field("Revision timestamp", date(source.revision_timestamp)));
  const grid = element("div", undefined, "data-grid");
  for (const [key, label] of [["categories", "Categories"], ["links", "Page links"], ["templates", "Templates"], ["images", "Image references"], ["external_links", "External links"]]) {
    const values = snapshot.data[key] || [];
    const card = element("article", undefined, "data-card");
    const header = element("header");
    header.append(element("h3", label), element("b", number(values.length)));
    const list = element("ul");
    values.slice(0, 4).forEach((value) => list.append(element("li", value)));
    if (!values.length) list.append(element("li", "None returned"));
    card.append(header, list, element("p", values.length > 4 ? `Showing 4 of ${number(values.length)} · all in JSON` : "All returned values shown", "subtle"));
    grid.append(card);
  }
  const content = element("article", undefined, "data-card");
  const header = element("header");
  header.append(element("h3", "Source & structure"), element("b", "{ }"));
  content.append(header, element("p", `${number(snapshot.structure?.templates.length || 0)} template calls, with parameter values and raw source.`, "subtle"), element("p", "Image references are filenames only. No artwork is downloaded or displayed.", "subtle"));
  grid.append(content);
  $("snapshot").replaceChildren(heading, identity, grid);
  const json = JSON.stringify(snapshot, null, 2);
  $("snapshot-json").textContent = json;
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  downloadUrl = URL.createObjectURL(new Blob([json + "\n"], {type: "application/json"}));
  $("download-snapshot").href = downloadUrl;
  $("download-snapshot").download = `mtgwiki-${source.pageid}.json`;
  $("provenance-fields").replaceChildren(
    field("Source API", source.api_url), field("Requested title", source.requested_title),
    field("Resolved title", source.resolved_title), field("Revision ID", source.revision_id),
    field("Revision timestamp", date(source.revision_timestamp)), field("Retrieval timestamp", date(source.retrieved_at))
  );
  const revisionUrl = new URL("index.php", source.api_url);
  revisionUrl.searchParams.set("oldid", source.revision_id);
  $("revision-link").href = safeUrl(revisionUrl.href) || "https://mtg.wiki";
}

function renderDataset(data) {
  if (data.schema_version !== 1 || !Array.isArray(data.snapshots) || !data.snapshots.length) throw new Error("Unsupported dataset");
  const select = $("page-select");
  select.replaceChildren();
  data.snapshots.forEach((snapshot, index) => {
    const option = element("option", snapshot.source.resolved_title);
    option.value = index;
    select.append(option);
  });
  select.addEventListener("change", () => renderSnapshot(data.snapshots[Number(select.value)]));
  select.disabled = false;
  renderSnapshot(data.snapshots[0]);
  const example = data.template_example;
  $("template-source").textContent = example.raw;
  $("template-label").textContent = example.label;
  const call = example.structure.templates[0];
  $("template-name").textContent = call.name;
  call.parameters.forEach((parameter) => {
    const row = element("tr");
    row.append(element("td", parameter.name), element("td", parameter.value.raw), element("td", parameter.value.text));
    $("parameters").append(row);
  });
  const category = data.category;
  for (const [count, label] of [[category.direct_members.length, "direct articles"], [category.immediate_subcategories.length, "immediate subcategories"], [category.members.length, "unique recursive articles"]]) {
    const item = element("div");
    item.append(element("b", number(count)), element("span", label));
    $("category-counts").append(item);
  }
  $("category-branches").append(element("p", "Immediate subcategories · preview"));
  category.immediate_subcategories.slice(0, 4).forEach((item) => $("category-branches").append(element("div", item.title, "branch")));
  if (!category.immediate_subcategories.length) $("category-branches").append(element("p", "No immediate subcategories returned."));
  category.members.slice(0, 12).forEach((item) => $("category-members").append(element("span", item.title)));
  $("category-note").textContent = `Showing ${Math.min(12, category.members.length)} of ${category.members.length} unique articles. Full results are in the saved dataset. Retrieved ${date(category.retrieved_at)}. Category membership may change; it is not revision-pinned.`;
  $("build-date").textContent = `Saved ${date(data.generated_at)} · mtgwiki ${data.package_version}`;
  if (data.rights?.text && safeUrl(data.rights.url)) {
    $("data-credit").append(document.createTextNode(" Text/data adapted under "));
    const license = element("a", data.rights.text);
    license.href = safeUrl(data.rights.url);
    $("data-credit").append(license, document.createTextNode(". Source links and raw content are retained."));
  }
  $("load-status").textContent = `${data.snapshots.length} saved snapshots · static dataset loaded`;
}

$("copy-install").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText("pip install mtgwiki");
    $("copy-install").textContent = "Copied";
  } catch {
    $("copy-install").textContent = "Select command";
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(document.querySelector(".install code"));
    range.setStartAfter(document.querySelector(".install code span"));
    selection.removeAllRanges();
    selection.addRange(range);
  }
});

fetch(new URL("./data/showcase.json", document.baseURI))
  .then((response) => {
    if (!response.ok) throw new Error(`Dataset request failed: ${response.status}`);
    return response.json();
  })
  .then(renderDataset)
  .catch(() => {
    $("load-status").textContent = "The saved dataset could not load. Reload this page, or use the dataset download below. For a local preview, serve docs over HTTP instead of opening index.html directly.";
    $("load-status").classList.add("error");
  });
