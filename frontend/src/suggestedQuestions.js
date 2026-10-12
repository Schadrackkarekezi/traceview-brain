// Shared between Home's landing chips (first 3) and ChatPanel's empty-state
// chips (all 5) - Home's list used to be its own hand-kept copy of the
// same first 3 questions, which meant editing one didn't touch the other.
//
// Deliberately one example per agent mode, in this order, so the first 3
// shown on Home alone already demonstrate the three modes that actually
// answer something (sql / hybrid / unstructured) - not three variations of
// the same "count something" query:
//   1. sql           - a plain number/list question, no story attached
//   2. hybrid        - needs a number AND the "why" behind it
//   3. unstructured  - a judgment/advice question with no number at all
//   4. chat (decline) - an honest "I can't answer that," not a guess
//   5. sql           - a second numeric example, for variety in ChatPanel
export const SUGGESTED_QUESTIONS = [
  "Which accounts have capacity contracts marked at_risk?",
  "Which accounts are under-consuming, and why?",
  "How should I handle a pricing objection?",
  "What's our customer satisfaction score?",
  "What's the total committed amount across active capacity contracts, by workload?",
];
