/** Calendar-facing schedule UI (used by index.html appendBubble). Expects todai.schedule.v1 JSON. */
function renderScheduleDisplay(display, options) {
  const opts = options || {};
  const root = document.createElement("div");
  root.className = "schedule-display";
  root.setAttribute("data-schedule-schema", display.schema || "todai.schedule.v1");
  if (!opts.hideTitle) {
    const title = document.createElement("div");
    title.className = "schedule-display-title";
    let titleText = display.title || "Calendar";
    if (display.progress && display.progress.total) {
      titleText +=
        " · " +
        display.progress.done +
        "/" +
        display.progress.total +
        " done (" +
        display.progress.percent +
        "%)";
    }
    title.textContent = titleText;
    root.appendChild(title);
  }

  let days = display.days || [];
  if (!days.length && display.events && display.events.length) {
    const byDate = {};
    display.events.forEach(function (ev) {
      const key = ev.date || "unknown";
      if (!byDate[key]) {
        byDate[key] = { date: key, weekday: ev.weekday, day: ev.day, month: ev.month, slots: [] };
      }
      byDate[key].slots.push({
        time:
          ev.time_range ||
          (ev.start_time && ev.end_time ? ev.start_time + " – " + ev.end_time : ""),
        title: ev.activity || "Event",
      });
    });
    days = Object.keys(byDate)
      .sort()
      .map(function (k) {
        return byDate[k];
      });
  }

  if (display.empty && days.length === 0) {
    const empty = document.createElement("div");
    empty.className = "schedule-empty";
    empty.textContent = "No events in this period.";
    root.appendChild(empty);
    return root;
  }

  const grid = document.createElement("div");
  grid.className = "cal-grid";
  days.forEach(function (day) {
    const row = document.createElement("div");
    row.className = "cal-day";

    const dateCol = document.createElement("div");
    dateCol.className = "cal-day-date";
    const wd = document.createElement("div");
    wd.className = "cal-day-weekday";
    wd.textContent = day.weekday || "";
    const dn = document.createElement("div");
    dn.className = "cal-day-num";
    dn.textContent = (day.day != null ? day.day : "") + (day.month ? " " + day.month : "");
    dateCol.appendChild(wd);
    dateCol.appendChild(dn);
    row.appendChild(dateCol);

    const slotsCol = document.createElement("div");
    slotsCol.className = "cal-day-slots";
    const slots = day.slots || [];
    if (!slots.length) {
      const none = document.createElement("div");
      none.className = "cal-day-empty";
      none.textContent = "No events";
      slotsCol.appendChild(none);
    } else {
      slots.forEach(function (slot) {
        const item = document.createElement("div");
        item.className = "cal-slot";
        const time = document.createElement("span");
        time.className = "cal-slot-time";
        time.textContent = slot.time || "";
        const tit = document.createElement("span");
        tit.className = "cal-slot-title";
        let titleText = slot.title || "";
        if (slot.status && slot.status !== "calendar") {
          titleText = "[" + slot.status + "] " + titleText;
        }
        tit.textContent = titleText;
        item.appendChild(time);
        item.appendChild(tit);
        if (slot.description) {
          const desc = document.createElement("div");
          desc.className = "cal-slot-desc";
          desc.style.fontSize = "0.72rem";
          desc.style.color = "#8b9cb3";
          desc.style.gridColumn = "1 / -1";
          desc.textContent = slot.description;
          item.appendChild(desc);
        }
        slotsCol.appendChild(item);
      });
    }
    row.appendChild(slotsCol);
    grid.appendChild(row);
  });
  root.appendChild(grid);

  (display.free_days || []).forEach(function (fd) {
    const banner = document.createElement("div");
    banner.className = "cal-free-banner";
    banner.textContent =
      (fd.weekday || "") + " · " + (fd.day || "") + " " + (fd.month || "") + " — " + (fd.label || "");
    root.appendChild(banner);
  });
  return root;
}
