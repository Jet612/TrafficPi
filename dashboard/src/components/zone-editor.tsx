"use client";

import { useEffect, useId, useMemo, useReducer, useRef, useState } from "react";
import { Ban, Camera, ChevronRight, Download, Octagon, Pentagon, Redo2, RefreshCw, Route, Undo2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status";
import { useUnsavedWarning } from "@/components/unsaved";
import { useStats } from "@/components/stats-provider";
import { ZoneCanvas } from "@/components/zone-canvas";
import { ZoneInspector } from "@/components/zone-inspector";
import { ApiError, api, referencePictureUrl, snapshotUrl } from "@/lib/api";
import { addLine, addStop, addZone, deleteElement, type ZoneKind } from "@/lib/editor";
import { formatDateTime } from "@/lib/format";
import { canRedo, canUndo, editorReducer, initialEditorState } from "@/lib/editor-state";
import {
  MAX_ELEMENTS,
  emptyProfile,
  hasErrors,
  problems as findProblems,
  sameProfile,
  type ElementKind,
} from "@/lib/profile";
import type { Profile } from "@/lib/types";

type Outcome = { kind: "ok" | "error"; text: string } | null;

interface Saved {
  profile: Profile;
  version: number;
  /** When the saved picture was taken (ms since the epoch), or null if there is none. */
  referenceAt: number | null;
}

const FILE_NAME = "trafficpi-profile.json";

/** Fields where typing should keep the browser's own undo and the Delete key to itself. */
const isTyping = (target: EventTarget | null): boolean =>
  target instanceof Element && target.closest("input, textarea, select, [contenteditable='true']") !== null;

export function ZoneEditor() {
  const [state, dispatch] = useReducer(editorReducer, undefined, () => initialEditorState(emptyProfile()));
  const [saved, setSaved] = useState<Saved | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [unreadable, setUnreadable] = useState<string | null>(null);
  const [open, setOpen] = useState(true); // it has a page of its own now
  const [busy, setBusy] = useState<null | "save" | "picture" | "import">(null);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [stillAt, setStillAt] = useState(0); // changes when a new still from the camera is wanted
  const [pictureFailed, setPictureFailed] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const introId = useId();
  const zone = useStats().today?.timezone ?? "UTC";

  const draft = state.present;
  const found = useMemo(() => findProblems(draft), [draft]);
  const blocked = hasErrors(found);
  const dirty = saved !== null && !sameProfile(draft, saved.profile);
  useUnsavedWarning(dirty); // leaving the page, by a link or by closing the tab, asks first

  useEffect(() => {
    const controller = new AbortController();
    api
      .profile(controller.signal)
      .then((result) => {
        dispatch({ type: "load", profile: result.profile });
        setSaved({ profile: result.profile, version: result.version, referenceAt: result.reference_at });
        setUnreadable(result.problem);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          console.error("could not load the lines and zones", error);
          setLoadFailed(true);
        }
      });
    return () => controller.abort();
  }, []);

  // Undo and redo work wherever the keyboard focus is, not only inside the editor. Chrome's Ctrl+Z
  // is document-wide: a press that missed the editor (the focus on the page itself, say) undoes
  // the last text typed in a field instead, and the draft changes under the user's hands. A text
  // field keeps its own undo, and a full screen view is not the editor's business.
  const ready = open && saved !== null;
  useEffect(() => {
    if (!ready) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || !(event.ctrlKey || event.metaKey) || isTyping(event.target)) return;
      if (document.fullscreenElement !== null || document.querySelector("[aria-modal='true']") !== null) return;
      const key = event.key.toLowerCase();
      if (key === "z") {
        event.preventDefault();
        dispatch({ type: event.shiftKey ? "redo" : "undo" });
      } else if (key === "y") {
        event.preventDefault();
        dispatch({ type: "redo" });
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [ready]);

  const pictureUrl = pictureFailed
    ? null
    : saved?.referenceAt
      ? referencePictureUrl(saved.referenceAt)
      : snapshotUrl(stillAt);

  function add(kind: ElementKind) {
    const added = kind === "line" ? addLine(draft) : kind === "stop" ? addStop(draft) : addZone(draft, kind as ZoneKind);
    if (!added) {
      setOutcome({ kind: "error", text: `There can be at most ${MAX_ELEMENTS} of these.` });
      return;
    }
    setOutcome(null);
    dispatch({ type: "change", profile: added.profile });
    dispatch({ type: "select", ref: added.ref });
  }

  async function save() {
    if (saved === null) return;
    setBusy("save");
    setOutcome(null);
    try {
      const result = await api.saveProfile(draft);
      // The server keeps its own tidied-up copy; show that one, so that what is on screen is what is stored.
      let stored = draft;
      let referenceAt = saved.referenceAt;
      try {
        const fresh = await api.profile();
        stored = fresh.profile;
        referenceAt = fresh.reference_at;
      } catch {
        // The save worked; keep what was sent.
      }
      dispatch({ type: "load", profile: stored });
      setSaved({ profile: stored, version: result.version, referenceAt });
      setUnreadable(null);
      setOutcome({
        kind: "ok",
        text: result.vision_notified
          ? `Saved as version ${result.version}. The camera is using it now.`
          : `Saved as version ${result.version}. The camera service could not be reached; it will use it when it starts.`,
      });
    } catch (error) {
      setOutcome({ kind: "error", text: error instanceof ApiError ? error.message : "Could not save." });
    } finally {
      setBusy(null);
    }
  }

  function discard() {
    if (saved === null) return;
    setOutcome(null);
    dispatch({ type: "change", profile: saved.profile });
  }

  function exportProfile() {
    const blob = new Blob([`${JSON.stringify(draft, null, 2)}\n`], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = FILE_NAME;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function importFile(file: File) {
    setBusy("import");
    setOutcome(null);
    try {
      let document: unknown;
      try {
        document = JSON.parse(await file.text());
      } catch (error) {
        const why = error instanceof Error ? error.message : "parse error";
        setOutcome({ kind: "error", text: `${file.name} is not valid JSON: ${why}` });
        return;
      }
      const checked = await api.checkProfile(document);
      if (!checked.ok || checked.profile === null) {
        setOutcome({ kind: "error", text: `${file.name} cannot be used: ${checked.problem ?? "it is not a profile"}` });
        return;
      }
      dispatch({ type: "change", profile: checked.profile });
      setOutcome({ kind: "ok", text: `Loaded ${file.name}. It is not saved yet.` });
    } catch (error) {
      setOutcome({ kind: "error", text: error instanceof ApiError ? error.message : "Could not check the file." });
    } finally {
      setBusy(null);
    }
  }

  async function keepCurrentView() {
    setBusy("picture");
    setOutcome(null);
    try {
      const result = await api.captureReference();
      setSaved((previous) => (previous ? { ...previous, referenceAt: result.reference_at } : previous));
      setPictureFailed(false);
      setOutcome({ kind: "ok", text: "The camera's current view is now the picture to draw on." });
    } catch (error) {
      const text =
        error instanceof ApiError && error.status === 503
          ? "The camera is not available right now."
          : error instanceof ApiError
            ? error.message
            : "Could not take the picture.";
      setOutcome({ kind: "error", text });
    } finally {
      setBusy(null);
    }
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (isTyping(event.target)) return; // let a text field have its own Delete and Escape
    if ((event.key === "Delete" || event.key === "Backspace") && state.selection) {
      // Only when the focus is on the picture or the list: a press on a button elsewhere is not about this.
      if (event.target instanceof Element && event.target.closest("[data-testid='zone-canvas'], [aria-label='Lines and zones']")) {
        event.preventDefault();
        dispatch({ type: "change", profile: deleteElement(draft, state.selection) });
      }
    } else if (event.key === "Escape" && state.selection) {
      dispatch({ type: "select", ref: null });
    }
  }

  const summary = saved ? `version ${saved.version}` : loadFailed ? "could not load" : "loading…";

  return (
    <Card>
      <details
        open={open}
        onToggle={(event) => {
          const isOpen = event.currentTarget.open;
          setOpen(isOpen);
          if (isOpen) setStillAt(Date.now());
        }}
        className="group"
      >
        <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 rounded-xl px-5 py-4">
          <h2 className="text-sm font-semibold text-ink">Lines and zones</h2>
          <span className="inline-flex items-center gap-2 text-sm text-ink-2">
            {dirty && <span>unsaved changes</span>}
            {summary}
            <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden="true" />
          </span>
        </summary>

        {open && saved === null && (
          <CardBody className="pt-0 text-sm text-ink-2">{loadFailed ? "The lines and zones could not be loaded." : "Loading…"}</CardBody>
        )}

        {open && saved !== null && (
          <CardBody className="pt-0">
            {/* Delete and Escape apply anywhere in the editor (undo and redo anywhere on the page). */}
            <div role="group" aria-label="Lines and zones editor" onKeyDown={onKeyDown}>
              <p id={introId} className="mb-3 max-w-3xl text-sm text-ink-2">
                A <strong className="font-medium text-ink">counting line</strong> counts the vehicles that cross it.{" "}
                A <strong className="font-medium text-ink">detection zone</strong> limits where vehicles are looked for.{" "}
                An <strong className="font-medium text-ink">exclusion zone</strong> ignores a spot, such as parked cars.
                A <strong className="font-medium text-ink">stop sign</strong> checks whether vehicles stop before its line.
                Add a shape, then drag it into place on the picture.
              </p>

              {unreadable && (
                <p role="status" className="mb-3 flex items-start gap-2 rounded-lg border border-edge bg-sunken p-3 text-sm text-ink">
                  <span className="font-medium">The saved lines and zones could not be read.</span>
                  <span className="text-ink-2">{unreadable} Saving replaces them.</span>
                </p>
              )}

              <div className="mb-3 flex flex-wrap items-center gap-2" role="toolbar" aria-label="Add and undo">
                <Button size="sm" onClick={() => add("line")}>
                  <Route className="size-4" aria-hidden="true" />
                  Add counting line
                </Button>
                <Button size="sm" onClick={() => add("detection")}>
                  <Pentagon className="size-4" aria-hidden="true" />
                  Add detection zone
                </Button>
                <Button size="sm" onClick={() => add("exclusion")}>
                  <Ban className="size-4" aria-hidden="true" />
                  Add exclusion zone
                </Button>
                <Button size="sm" onClick={() => add("stop")}>
                  <Octagon className="size-4" aria-hidden="true" />
                  Add stop sign
                </Button>
                <span className="mx-1 hidden h-5 w-px bg-edge sm:block" aria-hidden="true" />
                <div className="flex gap-2">
                  <Button size="sm" variant="ghost" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!canUndo(state)} onClick={() => dispatch({ type: "undo" })}>
                    <Undo2 className="size-4" aria-hidden="true" />
                    Undo
                  </Button>
                  <Button size="sm" variant="ghost" aria-label="Redo" title="Redo (Ctrl+Shift+Z)" disabled={!canRedo(state)} onClick={() => dispatch({ type: "redo" })}>
                    <Redo2 className="size-4" aria-hidden="true" />
                    Redo
                  </Button>
                </div>
              </div>

              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_21rem]">
                <div className="min-w-0">
                  <ZoneCanvas
                    profile={draft}
                    selection={state.selection}
                    problems={found}
                    pictureUrl={pictureUrl}
                    noPictureText={
                      saved.referenceAt
                        ? "The saved picture could not be loaded."
                        : "The camera is not available right now. You can still place lines and zones on the blank frame."
                    }
                    onPictureError={() => setPictureFailed(true)}
                    onSelect={(ref) => dispatch({ type: "select", ref })}
                    onLive={(profile) => dispatch({ type: "live", profile })}
                    onSettle={() => dispatch({ type: "settle" })}
                    onChange={(profile) => dispatch({ type: "change", profile })}
                  />
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-ink-2">
                    <span>
                      {pictureFailed ? (
                        "No picture."
                      ) : saved.referenceAt ? (
                        <>
                          Picture saved{" "}
                          <time dateTime={new Date(saved.referenceAt).toISOString()}>{formatDateTime(saved.referenceAt, zone)}</time>
                          {zone !== "UTC" && <> ({zone.replace("_", " ")})</>}.
                        </>
                      ) : (
                        "This is the live view, not saved."
                      )}
                    </span>
                    {(pictureFailed || !saved.referenceAt) && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setPictureFailed(false);
                          setStillAt(Date.now());
                        }}
                      >
                        <RefreshCw className="size-4" aria-hidden="true" />
                        Take a new picture
                      </Button>
                    )}
                    <Button size="sm" variant="ghost" busy={busy === "picture"} disabled={busy !== null} onClick={() => void keepCurrentView()}>
                      <Camera className="size-4" aria-hidden="true" />
                      {saved.referenceAt ? "Replace the saved picture" : "Keep this picture"}
                    </Button>
                  </div>
                </div>

                <ZoneInspector
                  profile={draft}
                  selection={state.selection}
                  problems={found}
                  onSelect={(ref) => dispatch({ type: "select", ref })}
                  onLive={(profile) => dispatch({ type: "live", profile })}
                  onSettle={() => dispatch({ type: "settle" })}
                  onChange={(profile) => dispatch({ type: "change", profile })}
                />
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-edge pt-4">
                <Button variant="primary" onClick={() => void save()} busy={busy === "save"} disabled={!dirty || blocked || busy !== null}>
                  Save
                </Button>
                <Button onClick={discard} disabled={!dirty || busy !== null}>
                  Discard changes
                </Button>
                {dirty && !blocked && <StatusBadge level="warning">Unsaved changes</StatusBadge>}
                {blocked && <StatusBadge level="critical">Fix the marked problems to save</StatusBadge>}
                <span className="grow" />
                <Button variant="ghost" onClick={exportProfile}>
                  <Download className="size-4" aria-hidden="true" />
                  Export
                </Button>
                <Button variant="ghost" busy={busy === "import"} disabled={busy !== null} onClick={() => fileInput.current?.click()}>
                  <Upload className="size-4" aria-hidden="true" />
                  Import
                </Button>
                <input
                  ref={fileInput}
                  type="file"
                  accept="application/json,.json"
                  aria-label="Profile file to import"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = ""; // so that the same file can be chosen again
                    if (file) void importFile(file);
                  }}
                />
              </div>

              <div aria-live="polite" className="mt-3 min-h-6 text-sm">
                {outcome && (
                  <p role={outcome.kind === "error" ? "alert" : undefined} className="text-ink">
                    <span
                      className={`mr-1.5 inline-block size-2 rounded-full align-middle ${outcome.kind === "error" ? "bg-critical" : "bg-good"}`}
                      aria-hidden="true"
                    />
                    <span className="font-medium">{outcome.kind === "error" ? "Not done. " : ""}</span>
                    {outcome.text}
                  </p>
                )}
              </div>
            </div>
          </CardBody>
        )}
      </details>
    </Card>
  );
}

