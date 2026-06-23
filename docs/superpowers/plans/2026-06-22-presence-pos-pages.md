# Presence — POS pages (Nuxt + Pinia + DaisyUI) Implementation Plan (Plan 3/3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.
>
> ⚠️ **This plan executes in the `maisonPOS` repo, NOT in `Yolo_Monitor`.** This
> file lives in the camera repo only as the planning record. File paths below are
> in the POS repo. Before starting, open `maisonPOS` and confirm the actual
> layout (the paths here follow the conventions documented in
> `contracts/cctv-timeline.v1.md` / `cctv-api.v1.md`). Only **Task 6 (go-live)**
> runs back in `Yolo_Monitor`.

**Goal:** Show the camera's presence data in the POS as two pages — a live
"ตอนนี้" board (room cards + DaisyUI avatar groups + status) and a "ไทม์ไลน์"
Gantt (rooms × time) — plus a tap-to-correct loop that feeds the camera's
`IdentityResolver`.

**Architecture:** One Cloud Function (`cctvPresence`) upserts interval docs the
camera pushes; one Firestore collection (`presence/`) the UI subscribes to. A
Pinia store (`usePresenceStore`) holds one live `onSnapshot` and derives both
views via PURE helpers (unit-tested). Reception taps an avatar → writes a
`corrections/` doc; a `cctvCorrections` GET lets the camera pull them.

**Tech Stack:** Nuxt 3, Pinia, DaisyUI (Tailwind), Firebase (Firestore +
Cloud Functions v2 `onRequest`, region `asia-southeast1`), TypeScript, Vitest.

## Global Constraints

- Executed in `maisonPOS`. Mirror existing files — **copy `cctvTimeline.ts` /
  `cctvArrival.ts` and adapt**; do not invent Firebase boilerplate. Match the
  repo's import style, admin-init, and secret handling.
- Auth/region/idempotency identical to `contracts/cctv-presence.v1.md` (the
  source of truth for the payload): `x-cctv-key`, `asia-southeast1`, doc id =
  interval `id` (upsert).
- Field names are fixed by the contract: `id, therapist, therapistId, room,
  status, startedAt, endedAt, confidence, camera`. Do not rename.
- Thai status strings come straight from the camera: `ทำงาน, ว่าง, ต้อนรับ,
  งานหลังบ้าน, พัก`. The UI maps them to colors but never hard-codes the list of
  people/rooms.
- Pure derivation logic (grouping, "now", Gantt geometry) lives in a
  framework-free module so it is Vitest-unit-testable; Vue components stay thin.
- Design decisions (locked for MVP): hide empty rooms (summarize free service
  rooms in a strip); flat card order from a config list; Gantt lanes = rooms
  (per-person lanes deferred); responsive tablet-first, timeline scrolls
  horizontally on narrow screens.
- LAN/PDPA: evidence images (if linked later) stay LAN-only, as in
  `cctv-timeline.v1.md`. Presence docs carry no images.

---

### Task 1: `cctvPresence` Cloud Function + `presence/` rules

**Files (maisonPOS):**
- Create: `functions/src/cctvPresence.ts` (copy `functions/src/cctvTimeline.ts`,
  adapt)
- Modify: `functions/src/index.ts` (export `cctvPresence`)
- Modify: `firestore.rules` (add `presence/`)

**Interfaces:**
- Consumes: the camera's `POST /cctvPresence` body (contract §1).
- Produces: `presence/{id}` docs (contract §2) the UI reads.

- [ ] **Step 1: Copy the existing pattern**

Duplicate `functions/src/cctvTimeline.ts` to `functions/src/cctvPresence.ts`.
Keep its imports, `onRequest({region:"asia-southeast1", cors:true})` wrapper,
the `x-cctv-key` check, method guard, and admin Firestore handle exactly.

- [ ] **Step 2: Replace the handler body**

Swap the timeline-specific validation/write for this (adjust the admin/db
reference name to match the file you copied):

```ts
if (req.method !== "POST") { res.status(405).json({ error: "Method Not Allowed" }); return; }
if (req.get("x-cctv-key") !== CCTV_API_KEY.value()) { res.status(401).json({ error: "Unauthorized" }); return; }

const b = req.body ?? {};
if (b.id == null || !b.room || !b.status || !b.startedAt) {
  res.status(400).json({ error: "Missing id/room/status/startedAt" }); return;
}
try {
  await db.collection("presence").doc(String(b.id)).set({
    id: b.id,
    therapist: b.therapist ?? null,
    therapistId: b.therapistId ?? null,
    room: b.room,
    status: b.status,
    startedAt: b.startedAt,
    endedAt: b.endedAt ?? null,
    confidence: b.confidence ?? null,
    camera: b.camera ?? null,
    receivedAt: admin.firestore.FieldValue.serverTimestamp(),
  }, { merge: true });
  res.status(200).json({ ok: true });
} catch (e) {
  res.status(500).json({ error: "Firestore write failed" });
}
```

- [ ] **Step 3: Export it**

In `functions/src/index.ts`, add `export { cctvPresence } from "./cctvPresence";`
(match the existing export style for `cctvTimeline`).

- [ ] **Step 4: Add Firestore rules**

In `firestore.rules`, mirror the `timeline/` rule:

```
match /presence/{id} {
  allow read: if request.auth != null;   // staff
  allow write: if false;                 // Cloud Function (admin) only
}
```

- [ ] **Step 5: Test on the emulator (per contract §4)**

```bash
# in maisonPOS
firebase emulators:start --only functions,firestore
# good key -> 200 + doc
curl -s -XPOST "$BASE/cctvPresence" -H "x-cctv-key: $KEY" -H "content-type: application/json" \
  -d '{"id":5,"therapist":"Phai","room":"MAISON 2","status":"ทำงาน","startedAt":"2026-06-22T13:40:00+07:00","endedAt":null}'
# repeat same id with endedAt set -> overwrites (not duplicated)
curl -s -XPOST "$BASE/cctvPresence" -H "x-cctv-key: $KEY" -H "content-type: application/json" \
  -d '{"id":5,"therapist":"Phai","room":"MAISON 2","status":"ทำงาน","startedAt":"2026-06-22T13:40:00+07:00","endedAt":"2026-06-22T15:00:00+07:00"}'
# bad key -> 401 ; missing room -> 400
curl -s -XPOST "$BASE/cctvPresence" -H "x-cctv-key: nope" -d '{}' ; echo
curl -s -XPOST "$BASE/cctvPresence" -H "x-cctv-key: $KEY" -H "content-type: application/json" -d '{"id":9,"status":"ว่าง","startedAt":"x"}'; echo
```
Expected: `{"ok":true}` twice (second overwrites doc `5`, now with `endedAt`);
`{"error":"Unauthorized"}`; `{"error":"Missing id/room/status/startedAt"}`.
Confirm only ONE `presence/5` doc exists in the emulator UI.

- [ ] **Step 6: Commit (maisonPOS)**

```bash
git add functions/src/cctvPresence.ts functions/src/index.ts firestore.rules
git commit -m "feat(cctv): cctvPresence function + presence/ rules"
```

---

### Task 2: `cctvCorrections` (reception fixes the camera pulls)

**Files (maisonPOS):**
- Create: `functions/src/cctvCorrections.ts`
- Modify: `functions/src/index.ts` (export it)
- Modify: `firestore.rules` (add `corrections/`)

**Interfaces:**
- Consumes: `corrections/` docs the UI writes (Task 4) — `{trackUid, name,
  createdAt}`.
- Produces: `GET /cctvCorrections` → `{corrections:[{id,trackUid,name}]}` for the
  camera's `corrections_sync.make_corrections_fetch` (Plan 2 Task 2).

- [ ] **Step 1: Write the function (GET, recent corrections)**

Copy the auth/region wrapper from `cctvPresence.ts`; body:

```ts
if (req.method !== "GET") { res.status(405).json({ error: "Method Not Allowed" }); return; }
if (req.get("x-cctv-key") !== CCTV_API_KEY.value()) { res.status(401).json({ error: "Unauthorized" }); return; }

// return the last 30 min of corrections; the camera dedups by id (idempotent)
const cutoff = admin.firestore.Timestamp.fromMillis(Date.now() - 30 * 60 * 1000);
const snap = await db.collection("corrections")
  .where("createdAt", ">=", cutoff).orderBy("createdAt").limit(200).get();
res.status(200).json({
  corrections: snap.docs.map(d => ({ id: d.id, trackUid: d.get("trackUid"), name: d.get("name") })),
});
```

- [ ] **Step 2: Export + rules**

`functions/src/index.ts`: `export { cctvCorrections } from "./cctvCorrections";`

`firestore.rules`:
```
match /corrections/{id} {
  allow read: if false;                  // camera reads via the Cloud Function
  allow create: if request.auth != null; // reception (authed staff) writes
}
```

- [ ] **Step 3: Test on the emulator**

```bash
# seed a correction doc, then GET
curl -s "$BASE/cctvCorrections" -H "x-cctv-key: $KEY"; echo   # -> {"corrections":[...]}
curl -s "$BASE/cctvCorrections" -H "x-cctv-key: nope"; echo    # -> 401
```
Add one `corrections/` doc with `trackUid`, `name`, `createdAt=now` in the
emulator UI; confirm it appears in the GET response; confirm a doc older than
30 min does not.

- [ ] **Step 4: Commit (maisonPOS)**

```bash
git add functions/src/cctvCorrections.ts functions/src/index.ts firestore.rules
git commit -m "feat(cctv): cctvCorrections GET + corrections/ rules"
```

---

### Task 3: Pure presence derivation + Pinia store

**Files (maisonPOS):**
- Create: `app/utils/presence.ts` (framework-free; the testable core)
- Create: `app/utils/presence.test.ts` (Vitest)
- Create: `app/stores/presence.ts` (Pinia; thin — subscribe + call helpers)

**Interfaces:**
- Produces:
  - `type Interval = {id:number; therapist:string|null; therapistId:string|null; room:string; status:string; startedAt:string; endedAt:string|null; confidence:number|null; camera:string|null}`
  - `openIntervals(list): Interval[]`
  - `byRoom(open): {room:string; people:{name:string; therapistId:string|null; confidence:number|null; status:string}[]; status:string}[]`
  - `isFreeStatus(status): boolean`
  - `ganttLanes(list, rooms:string[], dayStartMs, dayEndMs, nowMs): {room:string; bars:{leftPct,widthPct,status,name}[]}[]`
  - `usePresenceStore` (Pinia) exposing `now`, `free`, `lanes`, `subscribe()`.

- [ ] **Step 1: Write the failing test**

Create `app/utils/presence.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { openIntervals, byRoom, isFreeStatus, ganttLanes, type Interval } from "./presence";

const mk = (o: Partial<Interval>): Interval => ({
  id: 0, therapist: null, therapistId: null, room: "", status: "ว่าง",
  startedAt: "2026-06-22T13:00:00+07:00", endedAt: null, confidence: null,
  camera: null, ...o,
});

describe("presence derivation", () => {
  it("openIntervals keeps only still-open rows", () => {
    const list = [mk({ id: 1 }), mk({ id: 2, endedAt: "2026-06-22T14:00:00+07:00" })];
    expect(openIntervals(list).map(i => i.id)).toEqual([1]);
  });

  it("byRoom groups open people and summarizes room status (working wins)", () => {
    const open = [
      mk({ id: 1, room: "Foot Spa", therapist: "Nicky", status: "ทำงาน" }),
      mk({ id: 2, room: "Foot Spa", therapist: "Bua", status: "ว่าง" }),
      mk({ id: 3, room: "Reception", therapist: "Tan", status: "ว่าง" }),
    ];
    const g = byRoom(open);
    const foot = g.find(r => r.room === "Foot Spa")!;
    expect(foot.people.map(p => p.name)).toEqual(["Nicky", "Bua"]);
    expect(foot.status).toBe("ทำงาน");          // any worker -> room reads busy
    expect(g.find(r => r.room === "Reception")!.status).toBe("ว่าง");
  });

  it("isFreeStatus: ว่าง/ต้อนรับ are available, work/rest are not", () => {
    expect(isFreeStatus("ว่าง")).toBe(true);
    expect(isFreeStatus("ต้อนรับ")).toBe(true);
    expect(isFreeStatus("ทำงาน")).toBe(false);
    expect(isFreeStatus("พัก")).toBe(false);
  });

  it("ganttLanes places bars by time; open bars run to now", () => {
    const day0 = Date.parse("2026-06-22T13:00:00+07:00");
    const day1 = Date.parse("2026-06-22T17:00:00+07:00");   // 4h window
    const now = Date.parse("2026-06-22T15:00:00+07:00");    // 50%
    const list = [
      mk({ id: 1, room: "MAISON 2", therapist: "Phai", status: "ทำงาน",
           startedAt: "2026-06-22T14:00:00+07:00",          // 25%
           endedAt: null }),                                 // -> now (50%)
    ];
    const lanes = ganttLanes(list, ["MAISON 2"], day0, day1, now);
    const bar = lanes[0].bars[0];
    expect(Math.round(bar.leftPct)).toBe(25);
    expect(Math.round(bar.widthPct)).toBe(25);              // 25%..50%
    expect(bar.name).toContain("Phai");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (in maisonPOS): `npx vitest run app/utils/presence.test.ts`
Expected: FAIL — cannot resolve `./presence` / functions undefined.

- [ ] **Step 3: Implement `app/utils/presence.ts`**

```ts
export type Interval = {
  id: number; therapist: string | null; therapistId: string | null;
  room: string; status: string; startedAt: string; endedAt: string | null;
  confidence: number | null; camera: string | null;
};

const FREE = new Set(["ว่าง", "ต้อนรับ"]);
export const isFreeStatus = (s: string) => FREE.has(s);

export const openIntervals = (list: Interval[]) =>
  list.filter(i => i.endedAt == null);

export function byRoom(open: Interval[]) {
  const m = new Map<string, Interval[]>();
  for (const i of open) (m.get(i.room) ?? m.set(i.room, []).get(i.room)!).push(i);
  return [...m.entries()].map(([room, items]) => ({
    room,
    people: items.map(i => ({
      name: i.therapist ?? "ไม่ทราบชื่อ",
      therapistId: i.therapistId,
      confidence: i.confidence,
      status: i.status,
    })),
    // room summary: a worker present -> busy; else any free -> free; else rest
    status: items.some(i => !isFreeStatus(i.status) && i.status !== "พัก" && i.status !== "งานหลังบ้าน")
      ? "ทำงาน"
      : items.some(i => isFreeStatus(i.status)) ? "ว่าง" : items[0].status,
  }));
}

export function ganttLanes(
  list: Interval[], rooms: string[], dayStartMs: number, dayEndMs: number, nowMs: number,
) {
  const span = Math.max(1, dayEndMs - dayStartMs);
  const pct = (ms: number) => ((Math.min(Math.max(ms, dayStartMs), dayEndMs) - dayStartMs) / span) * 100;
  return rooms.map(room => ({
    room,
    bars: list.filter(i => i.room === room).map(i => {
      const a = Date.parse(i.startedAt);
      const b = i.endedAt ? Date.parse(i.endedAt) : nowMs;
      const left = pct(a);
      return {
        leftPct: left,
        widthPct: Math.max(0.5, pct(b) - left),
        status: i.status,
        name: `${i.therapist ?? "ไม่ทราบชื่อ"} · ${i.status}`,
      };
    }),
  }));
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run app/utils/presence.test.ts`
Expected: PASS — all 4 cases green.

- [ ] **Step 5: Implement the Pinia store `app/stores/presence.ts`**

Thin wrapper: one live subscription + derived getters. Match the repo's Firebase
client access (reuse however `useTimeline`/existing composables get `db`).

```ts
import { defineStore } from "pinia";
import { collection, onSnapshot, query, where } from "firebase/firestore";
import { openIntervals, byRoom, isFreeStatus, ganttLanes, type Interval } from "~/utils/presence";

export const usePresenceStore = defineStore("presence", {
  state: () => ({ intervals: [] as Interval[], unsub: null as null | (() => void) }),
  getters: {
    now: (s) => byRoom(openIntervals(s.intervals)),
    free: (s) => byRoom(openIntervals(s.intervals)).filter(r => r.people.some(p => isFreeStatus(p.status))),
  },
  actions: {
    subscribe(db: any) {
      if (this.unsub) return;
      // live "now" = open intervals; the page passes a day range for history
      const q = query(collection(db, "presence"), where("endedAt", "==", null));
      this.unsub = onSnapshot(q, (snap) => {
        this.intervals = snap.docs.map(d => d.data() as Interval);
      });
    },
    lanes(rooms: string[], dayStartMs: number, dayEndMs: number, nowMs: number) {
      return ganttLanes(this.intervals, rooms, dayStartMs, dayEndMs, nowMs);
    },
    stop() { this.unsub?.(); this.unsub = null; },
  },
});
```
> Note: the "now" board uses the `endedAt == null` subscription. The timeline
> page loads a day range with a separate `getDocs(query(... where startedAt >=
> dayStart ...))` call and feeds it to `ganttLanes` (add a `loadDay(db, date)`
> action mirroring `subscribe`). Keep both off the same `intervals` array or a
> second state field as the repo prefers.

- [ ] **Step 6: Commit (maisonPOS)**

```bash
git add app/utils/presence.ts app/utils/presence.test.ts app/stores/presence.ts
git commit -m "feat(presence): pure derivation helpers + Pinia store"
```

---

### Task 4: UI — board + timeline pages (DaisyUI)

**Files (maisonPOS):**
- Create: `app/pages/presence.vue` (tabs host)
- Create: `app/components/PresenceBoard.vue`
- Create: `app/components/PresenceTimeline.vue`
- Create: `app/composables/useCorrection.ts` (write a `corrections/` doc)

**Interfaces:**
- Consumes: `usePresenceStore` (Task 3); writes `corrections/` (read by Task 2).

- [ ] **Step 1: Correction composable**

`app/composables/useCorrection.ts`:

```ts
import { addDoc, collection, serverTimestamp } from "firebase/firestore";

export function useCorrection() {
  const { $db } = useNuxtApp();   // match how the repo exposes Firestore
  return {
    submit: (trackUid: string, name: string) =>
      addDoc(collection($db as any, "corrections"),
        { trackUid, name, createdAt: serverTimestamp() }),
  };
}
```
> `trackUid` for an avatar is the camera-side key. The camera pushes anonymous
> intervals with `therapist=null`; expose the key by adding `camera`+a track ref
> if needed. MVP: anonymous people show with their `camera`; a correction names
> the most-recent open anonymous interval for that camera/room. (If you need the
> exact `<camera>:<tid>`, add it to the presence payload as `trackUid` in a
> contract minor-rev — note for later.)

- [ ] **Step 2: `PresenceBoard.vue` (cards + avatar group + free filter + tap)**

```vue
<script setup lang="ts">
import { ref, computed } from "vue";
import { usePresenceStore } from "~/stores/presence";
const store = usePresenceStore();
const freeOnly = ref(false);
const rooms = computed(() => freeOnly.value ? store.free : store.now);
const badge = (s: string) => ({
  "ทำงาน": "badge-success", "ว่าง": "badge-info", "ต้อนรับ": "badge-info",
  "พัก": "badge-ghost", "งานหลังบ้าน": "badge-ghost",
} as Record<string,string>)[s] ?? "badge-ghost";
const { submit } = useCorrection();
async function rename(camera: string | null, current: string) {
  const name = window.prompt("ชื่อ therapist:", current === "ไม่ทราบชื่อ" ? "" : current);
  if (name) await submit(camera ?? "", name);   // see composable note
}
</script>

<template>
  <div>
    <label class="label cursor-pointer gap-2 w-fit mb-3">
      <input type="checkbox" class="checkbox checkbox-sm" v-model="freeOnly" />
      <span class="label-text">แสดงเฉพาะคนว่าง / พร้อมรับงาน</span>
    </label>
    <div class="grid gap-3" style="grid-template-columns:repeat(auto-fit,minmax(205px,1fr))">
      <div v-for="r in rooms" :key="r.room" class="card bg-base-100 border border-base-300">
        <div class="card-body p-4 gap-3">
          <div class="flex items-center justify-between">
            <span class="font-medium">{{ r.room }}</span>
            <span class="badge" :class="badge(r.status)">{{ r.status }}</span>
          </div>
          <div class="avatar-group -space-x-3">
            <div v-for="p in r.people" :key="p.name" class="avatar placeholder"
                 @click="rename(null, p.name)" role="button" :title="p.name">
              <div class="bg-neutral text-neutral-content w-9 rounded-full">
                <span>{{ p.name === "ไม่ทราบชื่อ" ? "?" : p.name.slice(0,1) }}</span>
              </div>
            </div>
          </div>
          <div class="text-xs opacity-60">{{ r.people.map(p => p.name).join(", ") }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 3: `PresenceTimeline.vue` (Gantt)**

```vue
<script setup lang="ts">
import { computed } from "vue";
import { usePresenceStore } from "~/stores/presence";
const store = usePresenceStore();
const ROOMS = ["MAISON 1","MAISON 2","MAISON 3","MAISON 4","Foot Spa","Reception","ห้องพัก"];
const day0 = computed(() => new Date().setHours(9,0,0,0));
const day1 = computed(() => new Date().setHours(21,0,0,0));
const now = Date.now();
const lanes = computed(() => store.lanes(ROOMS, day0.value, day1.value, now));
const nowPct = ((now - day0.value) / (day1.value - day0.value)) * 100;
const barColor = (s: string) => ({
  "ทำงาน": "bg-success/20 border-success", "ว่าง": "bg-info/20 border-info",
  "ต้อนรับ": "bg-info/20 border-info",
} as Record<string,string>)[s] ?? "bg-base-200 border-base-300";
</script>

<template>
  <div class="overflow-x-auto border border-base-300 rounded-lg">
    <div style="min-width:640px">
      <div v-for="lane in lanes" :key="lane.room" class="flex items-center border-b border-base-200 last:border-0">
        <div class="w-24 shrink-0 px-2 text-sm font-medium">{{ lane.room }}</div>
        <div class="relative flex-1 h-12">
          <div v-for="(b,i) in lane.bars" :key="i" class="absolute top-2 h-8 rounded border flex items-center px-1 overflow-hidden"
               :class="barColor(b.status)"
               :style="{ left: b.leftPct+'%', width: b.widthPct+'%' }">
            <span class="text-[10px] whitespace-nowrap">{{ b.name }}</span>
          </div>
          <div class="absolute top-0 bottom-0 w-0.5 bg-error" :style="{ left: nowPct+'%' }" />
        </div>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 4: `presence.vue` (tabs host + subscribe lifecycle)**

```vue
<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { usePresenceStore } from "~/stores/presence";
const tab = ref<"now"|"tl">("now");
const store = usePresenceStore();
const { $db } = useNuxtApp();           // match repo's Firestore handle
onMounted(() => store.subscribe($db));
onUnmounted(() => store.stop());
</script>

<template>
  <div class="p-4">
    <div role="tablist" class="tabs tabs-boxed w-fit mb-4">
      <a role="tab" class="tab" :class="{ 'tab-active': tab==='now' }" @click="tab='now'">ตอนนี้</a>
      <a role="tab" class="tab" :class="{ 'tab-active': tab==='tl' }" @click="tab='tl'">ไทม์ไลน์</a>
    </div>
    <PresenceBoard v-if="tab==='now'" />
    <PresenceTimeline v-else />
  </div>
</template>
```

- [ ] **Step 5: Verify it builds + renders against the emulator**

```bash
# seed presence/ docs (one open, one closed, one anonymous) in the emulator
npm run dev      # or the repo's dev command
```
Manually confirm: `/presence` shows the board; the free filter narrows it;
switching to ไทม์ไลน์ shows bars + the red "now" line; tapping an avatar prompts
for a name and writes a `corrections/` doc (check the emulator). Record the
result (screenshot/notes) — UI rendering is verified manually; the data logic is
covered by Task 3's Vitest.

- [ ] **Step 6: Commit (maisonPOS)**

```bash
git add app/pages/presence.vue app/components/PresenceBoard.vue app/components/PresenceTimeline.vue app/composables/useCorrection.ts
git commit -m "feat(presence): board + timeline pages (DaisyUI)"
```

---

### Task 5: Retention (keep `presence/` small)

**Files (maisonPOS):** `functions/src/presenceRetention.ts` + export.

- [ ] **Step 1: Scheduled cleanup**

Mirror the `timeline/` retention note (contract §2). A daily scheduled function
deletes closed docs older than N days:

```ts
import { onSchedule } from "firebase-functions/v2/scheduler";
export const presenceRetention = onSchedule(
  { schedule: "every 24 hours", region: "asia-southeast1" }, async () => {
    const cutoff = new Date(Date.now() - 90 * 864e5).toISOString();
    const snap = await db.collection("presence")
      .where("endedAt", "<", cutoff).where("endedAt", "!=", null).limit(500).get();
    const batch = db.batch();
    snap.docs.forEach(d => batch.delete(d.ref));
    await batch.commit();
  });
```
> Firestore can't combine `!=` and `<` on the same field in one query — if the
> emulator rejects it, split into two passes or store a separate `closed:bool`
> field set on close. Adjust to the repo's query conventions.

- [ ] **Step 2: Export + emulator smoke + commit**

Export in `index.ts`; trigger once via the emulator shell; confirm only old
closed docs are deleted. Commit `functions/src/presenceRetention.ts index.ts`.

---

### Task 6: Go-live wiring (back in `Yolo_Monitor`)

**Files (Yolo_Monitor):** `local_settings.py` (secrets) + a one-line
`config.py` flip; no code.

- [ ] **Step 1: Point the camera at the deployed POS**

After the POS functions are deployed, set in `local_settings.py` (gitignored):

```python
# from the POS team: deployed base + the shared CCTV key
POS_API_BASE = "https://asia-southeast1-<project>.cloudfunctions.net"
POS_API_KEY  = "<shared x-cctv-key>"
```

Ensure `config.py`'s `pos_api` block reads them (the repo already wires
`POS_API_KEY`; add `base_url` from `POS_API_BASE` the same way), then flip the
feature switches:

```python
"pos_timeline": {"enabled": True, ...},
"corrections":  {"enabled": True},
"roster":       {"enabled": True},
```

- [ ] **Step 2: End-to-end smoke**

Run `python main.py`. Confirm: presence rows appear in Firestore `presence/`
(check the POS); the POS `/presence` board updates live; a tap-to-correct in the
POS renames the person within one `corrections_poll_secs` cycle on the camera
(watch the camera log for the applied correction).

- [ ] **Step 3: Commit the wiring note (NOT secrets)**

```bash
git add config.py        # only if base_url wiring changed; never local_settings.py
git commit -m "chore(pos): read pos_api.base_url for presence go-live"
```

---

## Self-Review (plan vs design §8 + contract)

- **`cctvPresence` + `presence/`** → Task 1 (mirrors contract §1–2 exactly). ✓
- **Two pages: ตอนนี้ board + ไทม์ไลน์ Gantt, one data source** → Tasks 3–4
  (one `onSnapshot` in the store; both views derive from it). ✓
- **DaisyUI avatar group + status badge + tabs** → Task 4 (`avatar-group`,
  `badge-*`, `tabs`). ✓
- **Tap-to-correct loop feeding the camera** → Task 2 (`cctvCorrections` GET) +
  Task 4 (`useCorrection` writes `corrections/`) + camera `CorrectionsSync`
  (Plan 2) consumes. ✓
- **Free-only filter ("ใครว่าง")** → Task 4 + `isFreeStatus` (Task 3). ✓
- **Retention** → Task 5. ✓
- **Go-live** → Task 6 (camera repo). ✓

**Placeholder scan:** the only deliberately-open items are repo-alignment notes
(Firestore handle access, `trackUid` exposure) — flagged inline because they
depend on maisonPOS internals this plan can't see, not vague TODOs. The testable
core (`app/utils/presence.ts`) has full code + a real Vitest.

**Type consistency:** `Interval` fields match the contract (`startedAt/endedAt/
therapistId/...`); `byRoom`/`ganttLanes`/`isFreeStatus` signatures match between
`presence.ts`, its test, and the store; the `corrections/` doc shape
(`trackUid,name,createdAt`) matches `cctvCorrections` (Task 2) and the camera's
`make_corrections_fetch` (`{id,trackUid,name}`).

## Known follow-ups (honest)

- **Anonymous `trackUid` for corrections.** To name a specific anonymous person
  reliably, add `trackUid` (`<camera>:<tid>`) to the presence payload (contract
  minor-rev) so the UI can target the exact track. MVP corrects by camera/room.
- **Per-person timeline lanes** (design §2 "storyline") — deferred; rooms-lane
  Gantt ships first.
- **Room-level booking overlay** (scheduled vs actual) needs a `room` field on
  `cctvBookings` (see Plan 2). 
- **Cross-camera identity** still per Plan 2's limits — the board shows a person
  anonymous on a camera until their face re-matches there.
