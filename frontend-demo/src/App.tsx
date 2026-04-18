import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import './App.css'
import { Badge, GlowDot } from './components/ui'
import {
  DEMO_USERNAME,
  SCENE_A_PING,
  SCENE_C_REPLY,
  SCENE_E_RECAP_BODY,
  SCENE_E_RECAP_PREFIX,
  SCENE_F_MOOD_PING,
  FALLBACK_QUIZ,
  type DemoQuizQuestion,
} from './demoContent'

type AuthResponse = {
  token: string
  user_id: number
  username: string
}

type ChatMessage = {
  id: number
  user_id: number
  timestamp: string
  author: 'user' | 'system'
  sequence_number: number
  content: string
  processing_ms?: number | null
}

type ChatReplyResponse = {
  user_message: ChatMessage
  assistant_message: ChatMessage
}

type ChatSocketPayload = {
  type: 'chat_message' | 'ack'
  message?: ChatMessage
  echo?: string
}

type OverviewCourse = { id: number; name: string }
type OverviewEvent = {
  id: number
  course_name: string
  type: string
  name: string
  start_datetime: string
  end_datetime: string
}
type OverviewDeadline = {
  id: number
  course_name: string
  name: string
  datetime: string
}
type OverviewQuizStat = { total_taken: number; average_percent: number }
type OverviewResp = {
  now: string
  courses: OverviewCourse[]
  upcoming_events: OverviewEvent[]
  upcoming_deadlines: OverviewDeadline[]
  quiz: OverviewQuizStat
}

const TOKEN_KEY = 'tumtum-demo-token'

const SCENE_B_USER_LINE =
  "hmm yeah, the powerset thing got away from me. got time for a reset?"
const SCENE_G_USER_LINE =
  "yeah actually feeling way better. i'll hit the library this afternoon."

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

const TYPING_MS_PER_CHAR = 18
const POST_USER_GAP_MS = 1800
const POST_BUBBLE_BREATH_MS = 1400

function typingDurationMs(text: string): number {
  return Math.max(600, text.length * TYPING_MS_PER_CHAR)
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? '')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState('Booting demo...')
  const [quizOpen, setQuizOpen] = useState(false)
  const [quizAutoPlay, setQuizAutoPlay] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [isAutoRunning, setIsAutoRunning] = useState(false)
  const [overview, setOverview] = useState<OverviewResp | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const quizDoneResolverRef = useRef<(() => void) | null>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const animatedIdsRef = useRef<Set<number>>(new Set())

  useEffect(() => {
    if (token) {
      return
    }
    void (async () => {
      try {
        const response = await fetch('/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: DEMO_USERNAME }),
        })
        if (!response.ok) {
          throw new Error(`login ${response.status}`)
        }
        const body = (await response.json()) as AuthResponse
        localStorage.setItem(TOKEN_KEY, body.token)
        setToken(body.token)
      } catch (err) {
        setStatus(`Auto-login failed: ${toMessage(err)}`)
      }
    })()
  }, [token])

  useEffect(() => {
    if (!token) {
      return
    }
    setStatus(`Ready as ${DEMO_USERNAME} — press Play to run the demo.`)
  }, [token])

  const refreshOverview = useCallback(async () => {
    if (!token) {
      return
    }
    try {
      const response = await fetch('/demo/overview', {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) {
        throw new Error(`overview ${response.status}`)
      }
      const body = (await response.json()) as OverviewResp
      setOverview(body)
    } catch {
      // Sidebar is best-effort; silent fail keeps demo flow clean.
    }
  }, [token])

  useEffect(() => {
    void refreshOverview()
  }, [refreshOverview])

  useEffect(() => {
    const list = listRef.current
    if (!list) {
      return
    }
    list.scrollTop = list.scrollHeight
  }, [messages])

  useEffect(() => {
    if (!token) {
      return
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socketUrl = `${protocol}//${window.location.host}/ws?token=${encodeURIComponent(token)}`
    const socket = new WebSocket(socketUrl)
    socketRef.current = socket

    socket.addEventListener('message', (event) => {
      const payload = JSON.parse(event.data) as ChatSocketPayload
      if (payload.type !== 'chat_message' || !payload.message) {
        return
      }
      const incoming = payload.message
      setMessages((current) => {
        if (current.some((message) => message.id === incoming.id)) {
          return current
        }
        return [...current, incoming]
      })
    })

    socket.addEventListener('error', () => {
      setStatus('WebSocket error')
    })

    return () => {
      socket.close()
      if (socketRef.current === socket) {
        socketRef.current = null
      }
    }
  }, [token])

  async function waitForSocketOpen(timeoutMs = 1500) {
    const socket = socketRef.current
    if (!socket) {
      return
    }
    if (socket.readyState === WebSocket.OPEN) {
      return
    }
    await new Promise<void>((resolve) => {
      const onOpen = () => {
        socket.removeEventListener('open', onOpen)
        resolve()
      }
      socket.addEventListener('open', onOpen)
      setTimeout(() => {
        socket.removeEventListener('open', onOpen)
        resolve()
      }, timeoutMs)
    })
  }

  async function postJSON(url: string, body: unknown) {
    if (!token) {
      throw new Error('not logged in')
    }
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      throw new Error(`${url} ${response.status}`)
    }
    return response
  }

  async function sendScriptedTurn(userContent: string, systemContent: string) {
    const response = await postJSON('/demo/scripted-turn', {
      user_content: userContent,
      system_content: systemContent,
    })
    const body = (await response.json()) as {
      user_message: ChatMessage
      assistant_message: ChatMessage
    }
    setMessages((current) =>
      current.some((m) => m.id === body.user_message.id)
        ? current
        : [...current, body.user_message],
    )
    await sleep(typingDurationMs(userContent) + POST_USER_GAP_MS)
    setMessages((current) =>
      current.some((m) => m.id === body.assistant_message.id)
        ? current
        : [...current, body.assistant_message],
    )
  }

  async function sendSystemMessage(content: string) {
    await postJSON('/demo/system-message', { content })
  }

  async function saveQuizResults(correctCount: number, total: number) {
    await postJSON('/demo/quiz-results', {
      title: FALLBACK_QUIZ.title,
      topic: FALLBACK_QUIZ.topic,
      estimated_duration_minutes: 5,
      questions: FALLBACK_QUIZ.questions,
      correct_answers: correctCount,
      false_answers: total - correctCount,
    })
  }

  async function runFullDemo() {
    if (!token || isAutoRunning) {
      return
    }
    setIsAutoRunning(true)
    try {
      await waitForSocketOpen()
      setStatus('Scene A — TumTum proactive ping')
      await sendSystemMessage(SCENE_A_PING)
      await sleep(typingDurationMs(SCENE_A_PING) + POST_BUBBLE_BREATH_MS)

      setStatus('Scene B + C — Odin asks, TumTum replies')
      await sendScriptedTurn(SCENE_B_USER_LINE, SCENE_C_REPLY)
      await sleep(typingDurationMs(SCENE_C_REPLY) + POST_BUBBLE_BREATH_MS)

      setStatus('Scene D — 3-question MC quiz')
      setQuizAutoPlay(true)
      setQuizOpen(true)
      const quizDone = new Promise<void>((resolve) => {
        quizDoneResolverRef.current = resolve
      })
      await quizDone

      const recap = `${SCENE_E_RECAP_PREFIX}?/?${SCENE_E_RECAP_BODY}`
      setStatus('Scene E — performance recap (auto-fires from quiz)')
      await sleep(typingDurationMs(recap) + POST_BUBBLE_BREATH_MS)

      setStatus('Scene F — mood check-in')
      await sendSystemMessage(SCENE_F_MOOD_PING)
      await sleep(typingDurationMs(SCENE_F_MOOD_PING) + POST_BUBBLE_BREATH_MS)

      setStatus('Scene G — live LLM adaptive reply')
      const live = SCENE_G_USER_LINE
      setIsSending(true)
      try {
        const response = await fetch('/chat/messages', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ content: live }),
        })
        if (!response.ok) {
          throw new Error(`chat ${response.status}`)
        }
        const body = (await response.json()) as ChatReplyResponse
        setMessages((current) =>
          current.some((m) => m.id === body.user_message.id)
            ? current
            : [...current, body.user_message],
        )
        await sleep(typingDurationMs(live) + POST_USER_GAP_MS)
        setMessages((current) =>
          current.some((m) => m.id === body.assistant_message.id)
            ? current
            : [...current, body.assistant_message],
        )
        await sleep(typingDurationMs(body.assistant_message.content) + POST_BUBBLE_BREATH_MS)
      } finally {
        setIsSending(false)
      }
      setStatus('Demo complete.')
    } catch (err) {
      setStatus(`Demo run failed: ${toMessage(err)}`)
    } finally {
      setIsAutoRunning(false)
    }
  }

  async function handleSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token || !draft.trim() || isSending) {
      return
    }
    const outgoing = draft.trim()
    setDraft('')
    setIsSending(true)
    setStatus('Live LLM reply...')
    try {
      const response = await fetch('/chat/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ content: outgoing }),
      })
      if (!response.ok) {
        throw new Error(`chat ${response.status}`)
      }
      const body = (await response.json()) as ChatReplyResponse
      setMessages((current) => {
        const next = [...current]
        for (const message of [body.user_message, body.assistant_message]) {
          if (!next.some((m) => m.id === message.id)) {
            next.push(message)
          }
        }
        return next
      })
      setStatus('Live LLM reply delivered.')
    } catch (err) {
      setDraft(outgoing)
      setStatus(`Send failed: ${toMessage(err)}`)
    } finally {
      setIsSending(false)
    }
  }

  const handleQuizComplete = useCallback(
    async (answers: number[]) => {
      setQuizOpen(false)
      setQuizAutoPlay(false)
      const correctCount = answers.reduce(
        (acc, choice, idx) =>
          acc + (choice === FALLBACK_QUIZ.questions[idx].correct_index ? 1 : 0),
        0,
      )
      try {
        await saveQuizResults(correctCount, answers.length)
        setStatus(`Quiz saved: ${correctCount}/${answers.length}`)
        void refreshOverview()
      } catch (err) {
        setStatus(`Quiz save failed: ${toMessage(err)}`)
      }
      await sleep(700)
      try {
        await sendSystemMessage(
          `${SCENE_E_RECAP_PREFIX}${correctCount}/${answers.length} on the powerset drill.${SCENE_E_RECAP_BODY}`,
        )
      } catch (err) {
        setStatus(`Scene E recap failed: ${toMessage(err)}`)
      }
      const resolver = quizDoneResolverRef.current
      quizDoneResolverRef.current = null
      resolver?.()
    },
    [token, refreshOverview],
  )

  return (
    <div className="app-shell">
      <Sidebar overview={overview} />
      <main className="app-main">
        <header className="demo-header">
          <div>
            <p className="eyebrow">TumTum · Study Coach</p>
            <h1>Chat with Odin</h1>
          </div>
          <div className="status-pill" aria-live="polite">
            <button
              type="button"
              className="play-button"
              onClick={() => void runFullDemo()}
              disabled={!token || isAutoRunning}
            >
              {isAutoRunning ? '▸ Running demo...' : '▶ Play demo'}
            </button>
            <span className="status-text">{status}</span>
          </div>
        </header>

        <section className="chat-panel">
          <div className="message-list" ref={listRef}>
            {messages.length === 0 ? (
              <div className="empty-state">
                <p>No messages yet — press Play demo to start.</p>
              </div>
            ) : (
              messages.map((message) => (
                <article
                  key={message.id}
                  className={`message-bubble message-${message.author}`}
                >
                  <div className="message-meta">
                    <span>{message.author === 'user' ? DEMO_USERNAME : 'tumtum'}</span>
                    <span className="message-time">
                      {formatTime(message.timestamp)}
                      {message.processing_ms != null ? ` · ${message.processing_ms} ms` : ''}
                    </span>
                  </div>
                  <TypingText
                    content={message.content}
                    shouldAnimate={!animatedIdsRef.current.has(message.id)}
                    onDone={() => animatedIdsRef.current.add(message.id)}
                  />
                </article>
              ))
            )}
          </div>

          <form className="composer" onSubmit={handleSend}>
            <textarea
              id="message"
              name="message"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  void handleSend(event as unknown as FormEvent<HTMLFormElement>)
                }
              }}
              placeholder="Write a message..."
              rows={3}
              disabled={!token || isSending || isAutoRunning}
            />
            <div className="composer-actions">
              <p className="helper-text">
                Press ▶ Play demo for the full scripted 7-scene run.
              </p>
              <button type="submit" disabled={!token || isSending || isAutoRunning || !draft.trim()}>
                {isSending ? 'Sending...' : 'Send'}
              </button>
            </div>
          </form>
        </section>
      </main>

      {quizOpen ? (
        <QuizOverlay
          questions={FALLBACK_QUIZ.questions}
          title={FALLBACK_QUIZ.title}
          autoPlay={quizAutoPlay}
          onClose={() => {
            setQuizOpen(false)
            setQuizAutoPlay(false)
          }}
          onComplete={handleQuizComplete}
        />
      ) : null}
    </div>
  )
}

function TypingText({
  content,
  shouldAnimate,
  onDone,
}: {
  content: string
  shouldAnimate: boolean
  onDone?: () => void
}) {
  const [shown, setShown] = useState(shouldAnimate ? 0 : content.length)

  useEffect(() => {
    if (!shouldAnimate) {
      return
    }
    if (shown >= content.length) {
      onDone?.()
      return
    }
    const handle = setTimeout(() => {
      setShown((s) => Math.min(s + 1, content.length))
    }, TYPING_MS_PER_CHAR)
    return () => clearTimeout(handle)
  }, [shouldAnimate, shown, content, onDone])

  const isTyping = shouldAnimate && shown < content.length
  return (
    <p>
      {content.slice(0, shown)}
      {isTyping ? <span className="typing-caret" aria-hidden /> : null}
    </p>
  )
}

const MOCK_STUDY_PLAN: {
  time: string
  title: string
  status: 'done' | 'now' | 'next'
}[] = [
  { time: '09:30', title: 'DS · NFA → DFA lecture', status: 'done' },
  { time: '11:00', title: 'Coffee + flashcards', status: 'done' },
  { time: '13:00', title: 'Powerset drill (3Q)', status: 'now' },
  { time: '15:30', title: 'EInf H5 prep', status: 'next' },
  { time: '17:00', title: 'Library — DS Übungsblatt 8', status: 'next' },
]

const MOCK_FOCUS_STATS = {
  streak_days: 6,
  minutes_today: 92,
  minutes_goal: 180,
}

const MOCK_STREAK_WEEK: { label: string; minutes: number }[] = [
  { label: 'Fr', minutes: 145 },
  { label: 'Sa', minutes: 60 },
  { label: 'Su', minutes: 20 },
  { label: 'Mo', minutes: 165 },
  { label: 'Tu', minutes: 110 },
  { label: 'We', minutes: 180 },
  { label: 'Th', minutes: 92 },
]

const MOCK_TOPIC_FOCUS: {
  topic: string
  course: string
  mastery: number
  trend: 'up' | 'down' | 'flat'
}[] = [
  { topic: 'Powerset construction', course: 'DS', mastery: 62, trend: 'up' },
  { topic: 'ε-closures', course: 'DS', mastery: 78, trend: 'up' },
  { topic: 'Rekursion (H5)', course: 'EInf', mastery: 44, trend: 'flat' },
  { topic: 'Cache hierarchies', course: 'ERA', mastery: 71, trend: 'down' },
]

const MOCK_RECENT_WINS: { when: string; text: string }[] = [
  { when: '11:14', text: 'Mastered 8 flashcards on ε-closures' },
  { when: '10:02', text: 'Reviewed DS folien-14 (full set)' },
  { when: 'yesterday', text: 'Finished DS Übungsblatt 7 · 9/10' },
  { when: 'yesterday', text: 'Unlocked "5-day streak" milestone' },
]

const MOCK_QUICK_ACTIONS = [
  { key: 'pomodoro', label: 'Start 25-min focus' },
  { key: 'flashcards', label: 'Flashcards: DS (12 due)' },
  { key: 'tumtum', label: 'Ask TumTum anything' },
]

function Sidebar({ overview }: { overview: OverviewResp | null }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-brand-mark">TT</span>
        <div style={{ flex: 1 }}>
          <p className="eyebrow">TumTum</p>
          <p className="sidebar-brand-sub">Study coach</p>
        </div>
        <GlowDot color="success" />
      </div>

      <SidebarSection title="Focus today">
        <div className="sidebar-stats">
          <div className="sidebar-stat">
            <span className="sidebar-stat-value">{MOCK_FOCUS_STATS.streak_days}d</span>
            <span className="sidebar-stat-label">streak</span>
          </div>
          <div className="sidebar-stat">
            <span className="sidebar-stat-value">
              {MOCK_FOCUS_STATS.minutes_today}m
            </span>
            <span className="sidebar-stat-label">
              of {MOCK_FOCUS_STATS.minutes_goal}m
            </span>
          </div>
        </div>
        <div
          className="sidebar-progress"
          role="progressbar"
          aria-valuenow={MOCK_FOCUS_STATS.minutes_today}
          aria-valuemin={0}
          aria-valuemax={MOCK_FOCUS_STATS.minutes_goal}
        >
          <div
            className="sidebar-progress-fill"
            style={{
              width: `${Math.min(
                100,
                (MOCK_FOCUS_STATS.minutes_today / MOCK_FOCUS_STATS.minutes_goal) * 100,
              )}%`,
            }}
          />
        </div>
        <div className="sidebar-heatmap" aria-label="Last 7 days of focus minutes">
          {MOCK_STREAK_WEEK.map((day, idx) => {
            const level = Math.min(4, Math.floor(day.minutes / 45))
            return (
              <div
                key={idx}
                className={`sidebar-heatmap-cell sidebar-heatmap-lvl-${level}`}
                title={`${day.label}: ${day.minutes} min`}
              >
                <span className="sidebar-heatmap-label">{day.label}</span>
              </div>
            )
          })}
        </div>
      </SidebarSection>

      <SidebarSection title="Topic focus">
        <ul className="sidebar-list sidebar-topics">
          {MOCK_TOPIC_FOCUS.map((t, idx) => (
            <li key={idx} className="sidebar-topic">
              <div className="sidebar-topic-head">
                <span className="sidebar-topic-title">{t.topic}</span>
                <span className={`sidebar-topic-trend sidebar-topic-trend-${t.trend}`}>
                  {t.trend === 'up' ? '↑' : t.trend === 'down' ? '↓' : '→'}
                </span>
              </div>
              <div className="sidebar-topic-meta">
                <span className="sidebar-topic-course">{t.course}</span>
                <span className="sidebar-topic-pct">{t.mastery}%</span>
              </div>
              <div className="sidebar-topic-bar" aria-hidden>
                <div
                  className="sidebar-topic-bar-fill"
                  style={{ width: `${t.mastery}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      </SidebarSection>

      <SidebarSection title="Study plan">
        <ul className="sidebar-list sidebar-plan">
          {MOCK_STUDY_PLAN.map((slot, idx) => (
            <li
              key={idx}
              className={`sidebar-plan-item sidebar-plan-${slot.status}`}
            >
              <span className="sidebar-plan-time">{slot.time}</span>
              <span className="sidebar-plan-title">{slot.title}</span>
              <span className="sidebar-plan-badge">
                {slot.status === 'done'
                  ? '✓'
                  : slot.status === 'now'
                    ? 'now'
                    : ''}
              </span>
            </li>
          ))}
        </ul>
      </SidebarSection>

      <SidebarSection title="Courses">
        {overview && overview.courses.length > 0 ? (
          <ul className="sidebar-list">
            {overview.courses.map((course) => (
              <li key={course.id} className="sidebar-item">
                <span className="sidebar-dot" aria-hidden />
                <span>{course.name}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="sidebar-empty">No courses on file.</p>
        )}
      </SidebarSection>

      <SidebarSection title="Recent wins">
        <ul className="sidebar-list sidebar-wins">
          {MOCK_RECENT_WINS.map((win, idx) => (
            <li key={idx} className="sidebar-win">
              <span className="sidebar-win-when">{win.when}</span>
              <span className="sidebar-win-text">{win.text}</span>
            </li>
          ))}
        </ul>
      </SidebarSection>

      <SidebarSection title="Quick actions">
        <div className="sidebar-actions">
          {MOCK_QUICK_ACTIONS.map((a) => (
            <button key={a.key} type="button" className="sidebar-action">
              <span className="sidebar-action-label">{a.label}</span>
              <span className="sidebar-action-chev" aria-hidden>›</span>
            </button>
          ))}
        </div>
      </SidebarSection>

      <SidebarSection title="Upcoming events">
        {overview && overview.upcoming_events.length > 0 ? (
          <ul className="sidebar-list">
            {overview.upcoming_events.map((event) => (
              <li key={event.id} className="sidebar-item sidebar-item-stack">
                <div className="sidebar-item-row">
                  <span className="sidebar-item-title">{event.name}</span>
                  <Badge variant="accent">{event.type}</Badge>
                </div>
                <div className="sidebar-item-meta">
                  <span className="sidebar-item-course">{event.course_name}</span>
                  <span className="sidebar-item-time">{formatDayTime(event.start_datetime)}</span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="sidebar-empty">Nothing scheduled.</p>
        )}
      </SidebarSection>

      <SidebarSection title="Deadlines">
        {overview && overview.upcoming_deadlines.length > 0 ? (
          <ul className="sidebar-list">
            {overview.upcoming_deadlines.map((deadline) => (
              <li key={deadline.id} className="sidebar-item sidebar-item-stack">
                <div className="sidebar-item-row">
                  <span className="sidebar-item-title">{deadline.name}</span>
                  <Badge variant="danger">Due</Badge>
                </div>
                <div className="sidebar-item-meta">
                  <span className="sidebar-item-course">{deadline.course_name}</span>
                  <span className="sidebar-item-time">{formatDayTime(deadline.datetime)}</span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="sidebar-empty">No pending deadlines.</p>
        )}
      </SidebarSection>

      <SidebarSection title="Quiz performance">
        {overview ? (
          <div className="sidebar-stats">
            <div className="sidebar-stat">
              <span className="sidebar-stat-value">{overview.quiz.total_taken}</span>
              <span className="sidebar-stat-label">quizzes</span>
            </div>
            <div className="sidebar-stat">
              <span className="sidebar-stat-value">{overview.quiz.average_percent}%</span>
              <span className="sidebar-stat-label">avg score</span>
            </div>
          </div>
        ) : (
          <p className="sidebar-empty">Loading…</p>
        )}
      </SidebarSection>
    </aside>
  )
}

function SidebarSection({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="sidebar-section">
      <h2 className="sidebar-section-title">{title}</h2>
      {children}
    </section>
  )
}

type QuizOverlayProps = {
  questions: DemoQuizQuestion[]
  title: string
  autoPlay?: boolean
  onClose: () => void
  onComplete: (answers: number[]) => void
}

function QuizOverlay({ questions, title, autoPlay, onClose, onComplete }: QuizOverlayProps) {
  const [index, setIndex] = useState(0)
  const [answers, setAnswers] = useState<number[]>([])
  const [selected, setSelected] = useState<number | null>(null)

  const current = questions[index]
  const isLast = index === questions.length - 1
  const revealed = selected !== null

  useEffect(() => {
    if (!autoPlay) {
      return
    }
    let cancelled = false
    const pickDelay = 1400
    const nextDelay = 2200

    async function autoStep() {
      await sleep(pickDelay)
      if (cancelled) {
        return
      }
      const correct = current.correct_index
      setSelected(correct)
      await sleep(nextDelay)
      if (cancelled) {
        return
      }
      const nextAnswers = [...answers, correct]
      if (isLast) {
        onComplete(nextAnswers)
        return
      }
      setAnswers(nextAnswers)
      setSelected(null)
      setIndex((i) => i + 1)
    }

    void autoStep()
    return () => {
      cancelled = true
    }
  }, [autoPlay, index, current, answers, isLast, onComplete])

  function handleSelect(optionIdx: number) {
    if (revealed || autoPlay) {
      return
    }
    setSelected(optionIdx)
  }

  function handleNext() {
    if (selected === null || autoPlay) {
      return
    }
    const nextAnswers = [...answers, selected]
    if (isLast) {
      onComplete(nextAnswers)
      return
    }
    setAnswers(nextAnswers)
    setSelected(null)
    setIndex((i) => i + 1)
  }

  return (
    <div className="quiz-backdrop" role="dialog" aria-modal="true">
      <div className="quiz-card">
        <header className="quiz-header">
          <p className="eyebrow">{title}</p>
          <button type="button" className="ghost-button" onClick={onClose} disabled={autoPlay}>
            Close
          </button>
        </header>
        <p className="quiz-progress">
          Question {index + 1} of {questions.length}
        </p>
        <h2 className="quiz-question">{current.question}</h2>
        <div className="quiz-options">
          {current.options.map((option, optIdx) => {
            const isCorrect = optIdx === current.correct_index
            const isChosen = selected === optIdx
            let className = 'quiz-option'
            if (revealed) {
              if (isCorrect) {
                className += ' quiz-option-correct'
              } else if (isChosen) {
                className += ' quiz-option-wrong'
              }
            } else if (isChosen) {
              className += ' quiz-option-chosen'
            }
            return (
              <button
                key={optIdx}
                type="button"
                className={className}
                onClick={() => handleSelect(optIdx)}
                disabled={revealed || autoPlay}
              >
                <span className="quiz-option-letter">{String.fromCharCode(65 + optIdx)}</span>
                <span>{option}</span>
              </button>
            )
          })}
        </div>
        {revealed ? (
          <div className="quiz-explain">
            <p>
              <strong>
                {selected === current.correct_index ? 'Correct.' : 'Not quite.'}
              </strong>{' '}
              {current.explanation}
            </p>
            <p className="quiz-source">Source: {current.source_ref}</p>
          </div>
        ) : null}
        <div className="quiz-actions">
          <button type="button" onClick={handleNext} disabled={selected === null || autoPlay}>
            {isLast ? 'Finish quiz' : 'Next question'}
          </button>
        </div>
      </div>
    </div>
  )
}

function formatTime(iso: string) {
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function formatDayTime(iso: string) {
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function toMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }
  return 'Unexpected error.'
}

export default App
