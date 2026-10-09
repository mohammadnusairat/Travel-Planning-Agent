import { useLlmStatus } from "../llmStatus";

const CREDITS_TITLE = "Out of API credits";
const CREDITS_BODY =
  "This app has run out of Anthropic API credits (or hit its usage limit), so the AI agent can't plan trips right now. " +
  "Please try again later. Trips you start now will stop as soon as the agent needs to think.";
const KEY_TITLE = "AI service unavailable";
const KEY_BODY = "The server's Anthropic API key was rejected, so the AI agent can't plan trips. The app owner needs to fix the key.";

/** Site-wide banner shown while the server reports a credits or API-key problem. */
export function CreditsBanner() {
  const { creditsExhausted, keyRejected } = useLlmStatus();
  if (!creditsExhausted && !keyRejected) return null;
  return (
    <div className="banner banner-credits" role="alert">
      <strong>{creditsExhausted ? CREDITS_TITLE : KEY_TITLE}.</strong> {creditsExhausted ? CREDITS_BODY : KEY_BODY}
    </div>
  );
}

/** Inline warning next to "start a run" buttons. */
export function CreditsWarning() {
  const { creditsExhausted, keyRejected } = useLlmStatus();
  if (!creditsExhausted && !keyRejected) return null;
  return (
    <div className="note note-credits" role="status">
      {creditsExhausted
        ? "The app is currently out of API credits, so this run will most likely stop right away. You can still try in case credits were just added."
        : "The server's API key is currently being rejected, so this run will most likely fail."}
    </div>
  );
}

/** Prominent explanation on a run that was cut short (or left unscored) by a credits problem. */
export function RunCreditsNotice({ errorKind, judgeOnly, bookings }: { errorKind: string | null; judgeOnly: boolean; bookings: number }) {
  if (errorKind !== "credits_exhausted" && errorKind !== "auth_error") return null;
  const credits = errorKind === "credits_exhausted";
  return (
    <div className="credits-notice" role="alert">
      <div className="credits-icon" aria-hidden>
        {credits ? "💳" : "🔑"}
      </div>
      <div>
        <h2>{credits ? (judgeOnly ? "Out of API credits before scoring" : "Out of API credits") : KEY_TITLE}</h2>
        {judgeOnly ? (
          <p>
            The trip was planned in full, but the app ran out of Anthropic API credits before the LLM judge could score it.
            The itinerary below is complete.
          </p>
        ) : (
          <p>
            {credits
              ? "The app ran out of Anthropic API credits (or hit its usage limit) while planning, so the agent had to stop."
              : "The server's Anthropic API key was rejected, so the agent couldn't continue."}{" "}
            {bookings > 0
              ? `The ${bookings} booking${bookings === 1 ? "" : "s"} made before that are shown below, but the trip isn't complete.`
              : "Nothing was booked."}
          </p>
        )}
        <p className="muted small">{credits ? "Please try again later, once credits are available." : "Please let the app owner know."}</p>
      </div>
    </div>
  );
}
