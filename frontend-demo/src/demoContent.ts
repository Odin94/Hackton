// Scripted content for the TUM demo beats.
// Edit freely before rehearsal — no logic here, just strings + the hardcoded
// fallback quiz.

export const DEMO_USERNAME = 'odin'

export const SCENE_A_PING =
  "Hey Odin — I saw DS NFA→DFA on your calendar this morning. How'd it land?"

export const SCENE_C_REPLY =
  "That tracks — the prof usually runs out of time on the powerset construction. " +
  "I pulled up folien-14.pdf and folien-15a.pdf from the DS materials. " +
  "Want a quick 3-question drill on the rest before it slips?"

export const SCENE_E_RECAP_PREFIX =
  'Nice — ' // gets completed with the score at send-time

export const SCENE_E_RECAP_BODY =
  " You've now got the full NFA→DFA pipeline: start state is the ε-closure of " +
  'the NFA start, transitions are ε-closure of {δ(q,a) for q in subset}, and ' +
  'final states are any subset containing an NFA final state. Worst case 2^n ' +
  "subsets — usually way fewer. You've got DS Übungsblatt 8 due Tuesday; the " +
  'multi-character string traces are the next rep.'

export const SCENE_F_MOOD_PING =
  'Quick check in — you said earlier you felt abgehängt. How are you feeling now, after the drill?'

export type DemoQuizQuestion = {
  question: string
  options: [string, string, string, string]
  correct_index: number
  explanation: string
  source_ref: string
}

export const FALLBACK_QUIZ: {
  title: string
  topic: string
  questions: DemoQuizQuestion[]
} = {
  title: 'DS drill: NFA → DFA (Powerset Construction)',
  topic: 'NFA to DFA conversion, powerset construction',
  questions: [
    {
      question:
        'In the powerset construction, what is the start state of the resulting DFA?',
      options: [
        'The ε-closure of the NFA start state',
        'The NFA start state alone',
        'The set of all NFA states',
        'The set of all NFA final states',
      ],
      correct_index: 0,
      explanation:
        'The DFA start state must include every NFA state reachable from the ' +
        'start via ε-transitions — that is the ε-closure of the NFA start.',
      source_ref: 'Diskrete Strukturen · folien-14.pdf',
    },
    {
      question:
        'When is a subset (a DFA state from the powerset construction) marked as accepting?',
      options: [
        'When the subset contains at least one NFA final state',
        'Only when every NFA state in the subset is final',
        'When the subset contains the NFA start state',
        'When the subset is a singleton',
      ],
      correct_index: 0,
      explanation:
        'A subset is accepting iff it contains ≥1 NFA final state, because the ' +
        'NFA accepts if any of its parallel runs ends in a final state.',
      source_ref: 'Diskrete Strukturen · folien-14.pdf',
    },
    {
      question:
        'An NFA has n states. What is the worst-case number of states in the equivalent DFA from the powerset construction?',
      options: ['2^n', 'n', 'n^2', 'n!'],
      correct_index: 0,
      explanation:
        'Each DFA state corresponds to a subset of NFA states, so up to 2^n ' +
        'subsets exist. In practice most subsets are unreachable, so it is ' +
        'usually far fewer.',
      source_ref: 'Diskrete Strukturen · folien-15a.pdf',
    },
  ],
}
