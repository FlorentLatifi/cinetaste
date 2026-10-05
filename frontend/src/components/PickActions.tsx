import { useEffect, useRef, useState } from "react";
import type { RecommendationItem } from "../api/titles";
import { POST_WATCH_RATINGS } from "../features/taste/ratingScale";
import type { FeedbackAction } from "./ActionToast";

type Props = {
  item: RecommendationItem;
  onAct: (item: RecommendationItem, event: FeedbackAction) => void;
  /** Open the rating row (e.g. from the S shortcut). */
  ratingOpen?: boolean;
  onRatingOpenChange?: (open: boolean) => void;
};

/**
 * What you can do with a recommendation.
 *
 * Most picks are films the person has *not* seen, so the first question is
 * "do you want to watch it?", not "how would you rate it?". Rating is one
 * step away behind "Seen it" for the picks they already know.
 */
export function PickActions({ item, onAct, ratingOpen, onRatingOpenChange }: Props) {
  const [localOpen, setLocalOpen] = useState(false);
  const open = ratingOpen ?? localOpen;
  const setOpen = onRatingOpenChange ?? setLocalOpen;
  const firstRating = useRef<HTMLButtonElement>(null);
  const seenButton = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(open);
  const name = item.title.name;

  useEffect(() => {
    if (open && !wasOpen.current) firstRating.current?.focus();
    if (!open && wasOpen.current) seenButton.current?.focus();
    wasOpen.current = open;
  }, [open]);

  if (open) {
    return (
      <div className="pick-rate" role="group" aria-label={`Rate ${name}`}>
        <div className="pick-rate-head">
          <p className="pick-rate-prompt">How was it?</p>
          <button type="button" className="ob-back" onClick={() => setOpen(false)}>
            ← Back
          </button>
        </div>
        <div className="pick-rate-grid">
          {POST_WATCH_RATINGS.map((option, i) => (
            <button
              key={option.event}
              ref={i === 0 ? firstRating : undefined}
              type="button"
              className={`pick-rate-btn ${option.className}`}
              aria-label={`${option.label} — ${name}`}
              onClick={() => onAct(item, option.event)}
            >
              <span aria-hidden="true">{option.emoji}</span>
              <span>{option.label}</span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="pick-choice" role="group" aria-label={`Actions for ${name}`}>
      <button
        type="button"
        className="pick-btn pick-btn-primary"
        aria-label={`Want to watch — ${name}`}
        aria-keyshortcuts="w"
        onClick={() => onAct(item, "watchlist")}
      >
        <span aria-hidden="true">＋</span> Want to watch
      </button>
      <button
        ref={seenButton}
        type="button"
        className="pick-btn"
        aria-label={`Seen it — rate ${name}`}
        aria-keyshortcuts="s"
        aria-expanded={false}
        onClick={() => setOpen(true)}
      >
        Seen it
      </button>
      <button
        type="button"
        className="pick-btn pick-btn-quiet"
        aria-label={`Not for me — ${name}`}
        aria-keyshortcuts="n"
        onClick={() => onAct(item, "not_interested")}
      >
        Not for me
      </button>
    </div>
  );
}
