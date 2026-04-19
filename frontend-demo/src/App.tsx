import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import './App.css'
import { Badge, Button, Card, CardContent, CardHeader, GlowDot } from './components/ui'
import {
  SCENE_A_PING,
  SCENE_C_REPLY,
  SCENE_E_RECAP_BODY,
  SCENE_E_RECAP_PREFIX,
  SCENE_F_MOOD_PING,
  FALLBACK_QUIZ,
  type DemoQuizQuestion,
} from './demoContent'

type Page = 'login' | 'signup' | 'recommended' | 'chat' | 'demo'

type AuthResponse = {
  token: string
  user_id: number
  username: string
}

type ChatMessage = {
  id: number
  user_id: number
  timestamp: string
  author: 'user' | 'TumTum'
  sequence_number: number
  content: string
  processing_ms?: number | null
}

type ChatReplyResponse = {
  user_message: ChatMessage
  assistant_message: ChatMessage
}

type ChatHistoryResponse = {
  messages: ChatMessage[]
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

type RecommendedItem = {
  id: string
  kind: 'event' | 'deadline'
  title: string
  course: string
  primaryTime: string
  secondaryTime?: string
  badge: string
  tone: 'accent' | 'success' | 'danger'
  reason: string
}

const TOKEN_KEY = 'tumtum-demo-token'
const USERNAME_KEY = 'tumtum-demo-username'

const SCENE_B_USER_LINE =
  "hmm yeah, the powerset concept got away from me. got time for a quick review?"
const SCENE_G_USER_LINE =
  "yeah actually feeling way better. i'll hit the library this afternoon."

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

const TYPING_MS_PER_CHAR = 18
const POST_USER_GAP_MS = 1800
const POST_BUBBLE_BREATH_MS = 1400

const SCENE_BC_LIVE_TIMEOUT_MS = 10_000
const SCENE_D_QUIZ_GRACE_MS = 500
const SCENE_D_QUIZ_TOPIC = 'powerset construction NFA DFA epsilon closures'

const PAGE_LABELS: Partial<Record<Page, string>> = {
  recommended: 'Recommended events',
  chat: 'Chat',
  demo: 'Demo chat',
}

function typingDurationMs(text: string): number {
  return Math.max(600, text.length * TYPING_MS_PER_CHAR)
}

async function liveOrFallback<T>(
  live: Promise<T>,
  fallback: T,
  timeoutMs: number,
  label: string,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<T>((resolve) => {
    timer = setTimeout(() => {
      console.warn(`[${label}] live timed out after ${timeoutMs}ms, using fallback`)
      resolve(fallback)
    }, timeoutMs)
  })
  try {
    return await Promise.race([live, timeout])
  } finally {
    if (timer) clearTimeout(timer)
  }
}

type BackendQuizItem = {
  question: string
  answer: string
  options: string[]
  correct_index: number
  topic: string
  source_ref: string | null
}

async function fetchLiveQuiz(
  token: string,
  topic: string,
  n: number,
): Promise<DemoQuizQuestion[]> {
  const response = await fetch('/quiz', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ topic, n }),
  })
  if (!response.ok) {
    throw new Error(`quiz ${response.status}`)
  }
  const body = (await response.json()) as { items?: BackendQuizItem[] }
  const items = body.items ?? []
  if (items.length < n) {
    throw new Error(`quiz returned ${items.length}/${n} items`)
  }
  return items.slice(0, n).map((item, idx) => {
    if (
      typeof item.question !== 'string' ||
      !Array.isArray(item.options) ||
      item.options.length !== 4 ||
      !item.options.every((option) => typeof option === 'string') ||
      typeof item.correct_index !== 'number' ||
      item.correct_index < 0 ||
      item.correct_index > 3
    ) {
      throw new Error(`quiz item ${idx} shape invalid`)
    }
    return {
      question: item.question,
      options: item.options as [string, string, string, string],
      correct_index: item.correct_index,
      explanation: item.answer || 'Grounded in the course materials.',
      source_ref: item.source_ref ?? 'Lecture materials',
    }
  })
}

function App() {
  const [username, setUsername] = useState(() => localStorage.getItem(USERNAME_KEY) ?? '')
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? '')
  const [page, setPage] = useState<Page>(() =>
    pageFromHash(window.location.hash, Boolean(localStorage.getItem(TOKEN_KEY))),
  )
  const [loginName, setLoginName] = useState(() => localStorage.getItem(USERNAME_KEY) ?? '')
  const [signupName, setSignupName] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState(
    token
      ? 'Signed in. Open Recommended events to review what is next.'
      : 'Log in with an existing username or create a new account.',
  )
  const [authError, setAuthError] = useState('')
  const [isLoggingIn, setIsLoggingIn] = useState(false)
  const [isSigningUp, setIsSigningUp] = useState(false)
  const [isLoadingHistory, setIsLoadingHistory] = useState(false)
  const [isLoadingOverview, setIsLoadingOverview] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [pendingUserMessageId, setPendingUserMessageId] = useState<number | null>(null)
  const [isAutoRunning, setIsAutoRunning] = useState(false)
  const [quizOpen, setQuizOpen] = useState(false)
  const [quizAutoPlay, setQuizAutoPlay] = useState(false)
  const [overview, setOverview] = useState<OverviewResp | null>(null)
  const [activeQuiz, setActiveQuiz] = useState<{
    title: string
    topic: string
    questions: DemoQuizQuestion[]
  }>(FALLBACK_QUIZ)
  const [activeQuizIsLive, setActiveQuizIsLive] = useState(false)
  const listRef = useRef<HTMLDivElement | null>(null)
  const quizDoneResolverRef = useRef<(() => void) | null>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const animatedIdsRef = useRef<Set<number>>(new Set())

  useEffect(() => {
    if (username) {
      localStorage.setItem(USERNAME_KEY, username)
      setLoginName((current) => current || username)
    } else {
      localStorage.removeItem(USERNAME_KEY)
    }
  }, [username])

  useEffect(() => {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token)
    } else {
      localStorage.removeItem(TOKEN_KEY)
      setOverview(null)
      setMessages([])
      setDraft('')
      animatedIdsRef.current.clear()
    }
  }, [token])

  useEffect(() => {
    const next = token ? page : ensurePublicPage(page)
    const nextHash = `#${next}`
    if (window.location.hash !== nextHash) {
      window.history.replaceState(null, '', nextHash)
    }
  }, [page, token])

  useEffect(() => {
    const onHashChange = () => {
      setPage(pageFromHash(window.location.hash, Boolean(token)))
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [token])

  useEffect(() => {
    if (token && (page === 'login' || page === 'signup')) {
      setPage('recommended')
      return
    }
    if (!token && (page === 'recommended' || page === 'chat' || page === 'demo')) {
      setPage('login')
    }
  }, [page, token])

  const refreshOverview = useCallback(async () => {
    if (!token) {
      return
    }
    setIsLoadingOverview(true)
    try {
      const response = await fetch('/demo/overview', {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) {
        throw new Error(`overview ${response.status}`)
      }
      const body = (await response.json()) as OverviewResp
      setOverview(body)
    } catch (err) {
      setStatus(`Could not refresh recommendations: ${toMessage(err)}`)
    } finally {
      setIsLoadingOverview(false)
    }
  }, [token])

  useEffect(() => {
    if (!token) {
      return
    }
    void loadHistory(token)
    void refreshOverview()
  }, [token, refreshOverview])

  useEffect(() => {
    if (!token) {
      return
    }
    const intervalId = window.setInterval(() => {
      void refreshOverview()
    }, 5000)
    return () => window.clearInterval(intervalId)
  }, [token, refreshOverview])

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

  async function loadHistory(activeToken: string) {
    setIsLoadingHistory(true)
    try {
      const response = await fetch('/chat/history', {
        headers: {
          Authorization: `Bearer ${activeToken}`,
        },
      })
      if (!response.ok) {
        throw new Error(await readError(response, 'Could not load chat history.'))
      }
      const body = (await response.json()) as ChatHistoryResponse
      setMessages(body.messages)
    } catch (err) {
      setToken('')
      setAuthError(toMessage(err))
      setStatus('Your saved session expired. Please log in again.')
    } finally {
      setIsLoadingHistory(false)
    }
  }

  function applyAuth(body: AuthResponse, nextStatus: string) {
    setUsername(body.username)
    setToken(body.token)
    setAuthError('')
    setStatus(nextStatus)
    setPage('recommended')
  }

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = loginName.trim()
    if (!name) {
      setAuthError('Enter a username first.')
      return
    }
    setIsLoggingIn(true)
    setAuthError('')
    setStatus('Logging in...')
    try {
      const response = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: name }),
      })
      if (!response.ok) {
        throw new Error(await readError(response, 'Login failed.'))
      }
      const body = (await response.json()) as AuthResponse
      applyAuth(body, `Logged in as ${body.username}.`)
    } catch (err) {
      setAuthError(toMessage(err))
      setStatus('Could not log in.')
    } finally {
      setIsLoggingIn(false)
    }
  }

  async function handleSignup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = signupName.trim()
    if (!name) {
      setAuthError('Enter a username to sign up.')
      return
    }
    setIsSigningUp(true)
    setAuthError('')
    setStatus('Signing up...')
    try {
      const response = await fetch('/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: name }),
      })
      if (!response.ok) {
        throw new Error(await readError(response, 'Signup failed.'))
      }
      const body = (await response.json()) as AuthResponse
      setSignupName('')
      applyAuth(body, `Welcome, ${body.username}. Your account is ready.`)
    } catch (err) {
      setAuthError(toMessage(err))
      setStatus('Could not sign up.')
    } finally {
      setIsSigningUp(false)
    }
  }

  function handleLogout() {
    setToken('')
    setUsername('')
    setAuthError('')
    setStatus('Signed out. Log in again to refresh your recommendations and chat.')
    setPage('login')
  }

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

  async function sendSystemMessage(content: string) {
    await postJSON('/demo/system-message', { content })
  }

  async function saveQuizResults(
    quiz: { title: string; topic: string; questions: DemoQuizQuestion[] },
    correctCount: number,
    total: number,
  ) {
    await postJSON('/demo/quiz-results', {
      title: quiz.title,
      topic: quiz.topic,
      estimated_duration_minutes: 5,
      questions: quiz.questions,
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

      const liveQuizPromise = fetchLiveQuiz(token, SCENE_D_QUIZ_TOPIC, 3).catch((err) => {
        console.warn('[Scene D] live quiz prefetch failed:', err)
        return null
      })

      setStatus('Scene A · scripted — proactive ping')
      await sendSystemMessage(SCENE_A_PING)
      await sleep(typingDurationMs(SCENE_A_PING) + POST_BUBBLE_BREATH_MS)

      setStatus('Scene B+C · LIVE LLM + cognee — powerset Q')
      const liveChatPromise = postJSON('/chat/messages', { content: SCENE_B_USER_LINE })
        .then((response) => response.json() as Promise<ChatReplyResponse>)
        .catch((err) => {
          console.warn('[Scene B+C] /chat/messages failed:', err)
          return null
        })

      const tempUserMsg: ChatMessage = {
        id: -Date.now(),
        user_id: 0,
        timestamp: new Date().toISOString(),
        author: 'user',
        sequence_number: 0,
        content: SCENE_B_USER_LINE,
      }
      setMessages((current) => [...current, tempUserMsg])
      await sleep(typingDurationMs(SCENE_B_USER_LINE) + POST_USER_GAP_MS)

      setIsSending(true)
      const liveBody = await liveOrFallback(
        liveChatPromise,
        null,
        SCENE_BC_LIVE_TIMEOUT_MS,
        'scene-bc',
      )
      setIsSending(false)

      if (liveBody && liveBody.assistant_message?.content) {
        setMessages((current) =>
          current.some((message) => message.id === liveBody.assistant_message.id)
            ? current
            : [...current, liveBody.assistant_message],
        )
        await sleep(
          typingDurationMs(liveBody.assistant_message.content) + POST_BUBBLE_BREATH_MS,
        )
      } else {
        setStatus('Scene B+C · fallback scripted — LLM unreachable')
        const fallbackReply: ChatMessage = {
          id: -Date.now() - 1,
          user_id: 0,
          timestamp: new Date().toISOString(),
          author: 'TumTum',
          sequence_number: 0,
          content: SCENE_C_REPLY,
        }
        setMessages((current) => [...current, fallbackReply])
        await sleep(typingDurationMs(SCENE_C_REPLY) + POST_BUBBLE_BREATH_MS)
      }

      const resolvedQuiz = await liveOrFallback(
        liveQuizPromise,
        null,
        SCENE_D_QUIZ_GRACE_MS,
        'scene-d',
      )
      const nextQuiz = resolvedQuiz
        ? {
            title: FALLBACK_QUIZ.title,
            topic: FALLBACK_QUIZ.topic,
            questions: resolvedQuiz,
          }
        : FALLBACK_QUIZ
      setActiveQuiz(nextQuiz)
      setActiveQuizIsLive(resolvedQuiz != null)
      setStatus(
        resolvedQuiz
          ? 'Scene D · LIVE cognee quiz — powerset drill'
          : 'Scene D · scripted fallback — cognee unreachable',
      )
      setQuizAutoPlay(true)
      setQuizOpen(true)
      const quizDone = new Promise<void>((resolve) => {
        quizDoneResolverRef.current = resolve
      })
      await quizDone

      const recap = `${SCENE_E_RECAP_PREFIX}?/?${SCENE_E_RECAP_BODY}`
      setStatus('Scene E · scripted — performance recap')
      await sleep(typingDurationMs(recap) + POST_BUBBLE_BREATH_MS)

      setStatus('Scene F · scripted — mood check-in')
      await sendSystemMessage(SCENE_F_MOOD_PING)
      await sleep(typingDurationMs(SCENE_F_MOOD_PING) + POST_BUBBLE_BREATH_MS)

      setStatus('Scene G · LIVE LLM — adaptive reply')
      setIsSending(true)
      try {
        const response = await fetch('/chat/messages', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ content: SCENE_G_USER_LINE }),
        })
        if (!response.ok) {
          throw new Error(`chat ${response.status}`)
        }
        const body = (await response.json()) as ChatReplyResponse
        setMessages((current) =>
          current.some((message) => message.id === body.user_message.id)
            ? current
            : [...current, body.user_message],
        )
        await sleep(typingDurationMs(SCENE_G_USER_LINE) + POST_USER_GAP_MS)
        setMessages((current) =>
          current.some((message) => message.id === body.assistant_message.id)
            ? current
            : [...current, body.assistant_message],
        )
        await sleep(
          typingDurationMs(body.assistant_message.content) + POST_BUBBLE_BREATH_MS,
        )
      } finally {
        setIsSending(false)
      }
      setStatus('Demo complete.')
      void refreshOverview()
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
    const tempUserMessage: ChatMessage = {
      id: -Date.now(),
      user_id: 0,
      timestamp: new Date().toISOString(),
      author: 'user',
      sequence_number: 0,
      content: outgoing,
    }
    setDraft('')
    setIsSending(true)
    setPendingUserMessageId(tempUserMessage.id)
    animatedIdsRef.current.add(tempUserMessage.id)
    setMessages((current) => [...current, tempUserMessage])
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
        const next = current.filter((message) => message.id !== tempUserMessage.id)
        if (!next.some((item) => item.id === body.user_message.id)) {
          next.push(body.user_message)
        }
        if (!next.some((item) => item.id === body.assistant_message.id)) {
          next.push(body.assistant_message)
        }
        return next
      })
      setStatus('Live LLM reply delivered.')
    } catch (err) {
      setMessages((current) => current.filter((message) => message.id !== tempUserMessage.id))
      setDraft(outgoing)
      setStatus(`Send failed: ${toMessage(err)}`)
    } finally {
      setIsSending(false)
      setPendingUserMessageId(null)
    }
  }

  const handleQuizComplete = useCallback(
    async (answers: number[]) => {
      setQuizOpen(false)
      setQuizAutoPlay(false)
      const correctCount = answers.reduce(
        (acc, choice, idx) =>
          acc + (choice === activeQuiz.questions[idx].correct_index ? 1 : 0),
        0,
      )
      try {
        await saveQuizResults(activeQuiz, correctCount, answers.length)
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
    [activeQuiz, refreshOverview],
  )

  const recommendedItems = buildRecommendations(overview)

  return (
    <div className="app-shell">
      <Sidebar
        overview={overview}
        activePage={page}
        isAuthenticated={Boolean(token)}
        username={username}
        onNavigate={setPage}
        onLogout={handleLogout}
      />

      <main className="app-main">
        <MainHeader
          page={page}
          username={username}
          status={status}
          isAuthenticated={Boolean(token)}
          isBusy={isAutoRunning}
          onPlayDemo={() => void runFullDemo()}
          onNavigate={setPage}
        />

        {page === 'login' ? (
          <AuthPage
            key="login"
            title="Log in"
            eyebrow="Backend-compatible auth"
            description="Uses the same username-only POST /login flow as the main frontend."
            value={loginName}
            onChange={setLoginName}
            placeholder="existing username"
            submitLabel={isLoggingIn ? 'Logging in...' : 'Log in'}
            helper="Use an existing username from the backend."
            onSubmit={handleLogin}
            isSubmitting={isLoggingIn}
            error={authError}
            secondaryAction={
              <Button type="button" variant="ghost" onClick={() => setPage('signup')}>
                Need an account?
              </Button>
            }
          />
        ) : null}

        {page === 'signup' ? (
          <AuthPage
            key="signup"
            title="Create account"
            eyebrow="Simple signup"
            description="Creates a backend user with POST /signup and immediately stores the returned token."
            value={signupName}
            onChange={setSignupName}
            placeholder="pick a username"
            submitLabel={isSigningUp ? 'Creating...' : 'Sign up'}
            helper="Usernames are unique and trimmed server-side."
            onSubmit={handleSignup}
            isSubmitting={isSigningUp}
            error={authError}
            secondaryAction={
              <Button type="button" variant="ghost" onClick={() => setPage('login')}>
                Already have one?
              </Button>
            }
          />
        ) : null}

        {page === 'recommended' ? (
          <RecommendedEventsPage
            username={username}
            isAuthenticated={Boolean(token)}
            isLoading={isLoadingOverview}
            overview={overview}
            items={recommendedItems}
            onNavigate={setPage}
          />
        ) : null}

        {page === 'chat' ? (
          <section className="chat-panel">
            <div className="message-list" ref={listRef}>
              {!token ? (
                <div className="empty-state">
                  <p>Log in first to use chat.</p>
                </div>
              ) : isLoadingHistory ? (
                <div className="empty-state">
                  <p>Loading saved messages...</p>
                </div>
              ) : messages.length === 0 ? (
                <div className="empty-state">
                  <p>No chat history yet. Ask a question to create the first turn.</p>
                </div>
              ) : (
                messages.map((message) => (
                  <article
                    key={message.id}
                    className={`message-bubble message-${message.author}`}
                  >
                    <div className="message-meta">
                      <span className="message-meta-lead">
                        <span>{message.author === 'user' ? username || 'user' : 'TumTum'}</span>
                        {message.author !== 'user' && message.processing_ms != null ? (
                          <Badge variant="success">{message.processing_ms} ms</Badge>
                        ) : null}
                      </span>
                      <span className="message-time">
                        #{message.sequence_number} · {formatTime(message.timestamp)}
                      </span>
                    </div>
                    <p>{displayMessageContent(message.content)}</p>
                  </article>
                ))
              )}
              {pendingUserMessageId != null ? (
                <article className="message-bubble message-system typing-indicator-bubble">
                  <div className="message-meta">
                    <span className="message-meta-lead">
                      <span>TumTum</span>
                      <Badge variant="muted">typing</Badge>
                    </span>
                  </div>
                  <div className="typing-indicator-dots" aria-label="TumTum is typing">
                    <span />
                    <span />
                    <span />
                  </div>
                </article>
              ) : null}
            </div>

            <form className="composer" onSubmit={handleSend}>
              <textarea
                id="chat-message"
                name="chat-message"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Ask about your study notes, diary, quizzes, or schedules..."
                rows={4}
                disabled={!token || isSending}
              />
              <div className="composer-actions">
                <p className="helper-text">
                  {token
                    ? 'Each send stores your message and the backend reply.'
                    : 'Log in first to enable sending.'}
                </p>
                <button type="submit" disabled={!token || isSending || !draft.trim()}>
                  {isSending ? 'Sending...' : 'Send'}
                </button>
              </div>
            </form>
          </section>
        ) : null}

        {page === 'demo' ? (
          <section className="chat-panel">
            <div className="message-list" ref={listRef}>
              {!token ? (
                <div className="empty-state">
                  <p>Log in first to use the demo chat.</p>
                </div>
              ) : isLoadingHistory ? (
                <div className="empty-state">
                  <p>Loading saved messages...</p>
                </div>
              ) : messages.length === 0 ? (
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
                      <span className="message-meta-lead">
                        <span>{message.author === 'user' ? username || 'you' : 'tumtum'}</span>
                        {message.author !== 'user' ? (
                          message.processing_ms != null ? (
                            <Badge variant="success">
                              LIVE LLM · {message.processing_ms} ms
                            </Badge>
                          ) : (
                            <Badge variant="muted">scripted</Badge>
                          )
                        ) : null}
                      </span>
                      <span className="message-time">{formatTime(message.timestamp)}</span>
                    </div>
                    <TypingText
                      content={displayMessageContent(message.content)}
                      shouldAnimate={!animatedIdsRef.current.has(message.id)}
                      onDone={() => animatedIdsRef.current.add(message.id)}
                    />
                  </article>
                ))
              )}
              {pendingUserMessageId != null ? (
                <article className="message-bubble message-system typing-indicator-bubble">
                  <div className="message-meta">
                    <span className="message-meta-lead">
                      <span>tumtum</span>
                      <Badge variant="muted">typing</Badge>
                    </span>
                  </div>
                  <div className="typing-indicator-dots" aria-label="TumTum is typing">
                    <span />
                    <span />
                    <span />
                  </div>
                </article>
              ) : null}
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
                <button
                  type="submit"
                  disabled={!token || isSending || isAutoRunning || !draft.trim()}
                >
                  {isSending ? 'Sending...' : 'Send'}
                </button>
              </div>
            </form>
          </section>
        ) : null}
      </main>

      {quizOpen ? (
        <QuizOverlay
          questions={activeQuiz.questions}
          title={activeQuiz.title}
          isLive={activeQuizIsLive}
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

function MainHeader({
  page,
  username,
  status,
  isAuthenticated,
  isBusy,
  onPlayDemo,
  onNavigate,
}: {
  page: Page
  username: string
  status: string
  isAuthenticated: boolean
  isBusy: boolean
  onPlayDemo: () => void
  onNavigate: (page: Page) => void
}) {
  return (
    <header className="demo-header">
      <div>
        <p className="eyebrow">TumTum · Frontend demo</p>
        <h1>{PAGE_LABELS[page]}</h1>
      </div>
      <div className="status-pill" aria-live="polite">
        {page === 'demo' && isAuthenticated ? (
          <button type="button" className="play-button" onClick={onPlayDemo} disabled={isBusy}>
            {isBusy ? '▸ Running demo...' : '▶ Play demo'}
          </button>
        ) : null}
        {page === 'recommended' && isAuthenticated ? (
          <button type="button" className="play-button" onClick={() => onNavigate('chat')}>
            Open chat
          </button>
        ) : null}
        <span className="status-text">
          {isAuthenticated && username ? `${username} · ${status}` : status}
        </span>
      </div>
    </header>
  )
}

function AuthPage({
  title,
  eyebrow,
  description,
  value,
  onChange,
  placeholder,
  submitLabel,
  helper,
  onSubmit,
  isSubmitting,
  error,
  secondaryAction,
}: {
  title: string
  eyebrow: string
  description: string
  value: string
  onChange: (value: string) => void
  placeholder: string
  submitLabel: string
  helper: string
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  isSubmitting: boolean
  error: string
  secondaryAction?: ReactNode
}) {
  return (
    <section className="auth-page">
      <Card className="auth-card auth-card-highlight">
        <CardHeader
          title={title}
          subtitle={description}
          action={<Badge variant="accent">{eyebrow}</Badge>}
        />
        <CardContent>
          <form className="auth-form" onSubmit={onSubmit}>
            <label className="auth-label" htmlFor="auth-username">
              Username
            </label>
            <input
              id="auth-username"
              className="auth-input"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              placeholder={placeholder}
              autoComplete="username"
              disabled={isSubmitting}
            />
            <div className="auth-actions">
              <Button type="submit" disabled={isSubmitting || !value.trim()}>
                {submitLabel}
              </Button>
              {secondaryAction}
            </div>
            <p className="helper-text">{helper}</p>
            {error ? <p className="auth-error">{error}</p> : null}
          </form>
        </CardContent>
      </Card>
    </section>
  )
}

function RecommendedEventsPage({
  username,
  isAuthenticated,
  isLoading,
  overview,
  items,
  onNavigate,
}: {
  username: string
  isAuthenticated: boolean
  isLoading: boolean
  overview: OverviewResp | null
  items: RecommendedItem[]
  onNavigate: (page: Page) => void
}) {
  const data = overview ?? buildFallbackOverview()
  const nextEvent = data.upcoming_events[0]
  const nextDeadline = data.upcoming_deadlines[0]

  if (!isAuthenticated) {
    return (
      <section className="auth-page">
        <Card className="auth-card auth-card-highlight">
          <CardHeader
            title="Recommended events"
            subtitle="Log in to load the personalized overview from the backend."
          />
          <CardContent>
            <div className="auth-actions">
              <Button type="button" onClick={() => onNavigate('login')}>
                Go to login
              </Button>
              <Button type="button" variant="ghost" onClick={() => onNavigate('signup')}>
                Create account
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>
    )
  }

  return (
    <section className="recommended-page">
      <div className="recommended-hero">
        <div>
          <p className="eyebrow">Personalized view</p>
          <h2>{username ? `${username}'s next best moves` : 'Your next best moves'}</h2>
        </div>
        <div className="recommended-actions">
          {isLoading ? <Badge variant="muted">Refreshing...</Badge> : null}
          <Button type="button" onClick={() => onNavigate('chat')}>
            Open chat
          </Button>
        </div>
      </div>

      <div className="recommended-summary-grid">
        <Card>
          <CardHeader title="Courses" subtitle="Tracked in your backend profile" />
          <CardContent>
            <p className="summary-number">{data.courses.length}</p>
            <p className="summary-caption">
              {data.courses.map((course) => course.name).slice(0, 3).join(' · ') || 'No courses yet'}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader title="Next event" subtitle={nextEvent ? nextEvent.course_name : 'No event found'} />
          <CardContent>
            <p className="summary-number">
              {nextEvent ? formatDayTime(nextEvent.start_datetime) : 'None'}
            </p>
            <p className="summary-caption">{nextEvent?.name ?? 'Add schedule items to improve recommendations.'}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader
            title="Next deadline"
            subtitle={nextDeadline ? nextDeadline.course_name : 'No deadline found'}
          />
          <CardContent>
            <p className="summary-number">
              {nextDeadline ? formatDayTime(nextDeadline.datetime) : 'None'}
            </p>
            <p className="summary-caption">
              {nextDeadline?.name ?? 'Deadlines will appear here once they exist in the backend.'}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader title="Quiz momentum" subtitle="Pulled from saved quiz results" />
          <CardContent>
            <p className="summary-number">{data.quiz.average_percent}%</p>
            <p className="summary-caption">{data.quiz.total_taken} quizzes recorded</p>
          </CardContent>
        </Card>
      </div>

      <div className="recommended-grid">
        {items.map((item) => (
          <Card key={item.id} className="recommended-card">
            <CardHeader
              title={item.title}
              subtitle={item.course}
              action={
                <Badge variant={item.tone === 'danger' ? 'danger' : item.tone === 'success' ? 'success' : 'accent'}>
                  {item.badge}
                </Badge>
              }
            />
            <CardContent className="recommended-card-body">
              <div className="recommended-time-row">
                <span className="recommended-time-primary">{item.primaryTime}</span>
                {item.secondaryTime ? (
                  <span className="recommended-time-secondary">{item.secondaryTime}</span>
                ) : null}
              </div>
              <p className="recommended-reason">{item.reason}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
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
      setShown((current) => Math.min(current + 1, content.length))
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
  { topic: 'Reihen-Konvergenz', course: 'Analysis', mastery: 58, trend: 'up' },
  { topic: 'Chomsky hierarchy', course: 'DS', mastery: 81, trend: 'up' },
]

const MOCK_RECENT_WINS: { when: string; text: string }[] = [
  { when: '11:14', text: 'Mastered 8 flashcards on ε-closures' },
  { when: '10:02', text: 'Reviewed DS folien-14 (full set)' },
  { when: 'yesterday', text: 'Finished DS Übungsblatt 7 · 9/10' },
  { when: 'yesterday', text: 'Unlocked "5-day streak" milestone' },
]

const MOCK_COURSES: OverviewCourse[] = [
  { id: -1, name: 'Diskrete Strukturen' },
  { id: -2, name: 'Einführung in die Informatik' },
  { id: -3, name: 'Analysis für Informatik' },
]

const MOCK_EVENTS: OverviewEvent[] = [
  {
    id: -1,
    course_name: 'Diskrete Strukturen',
    type: 'Tutorial',
    name: 'DS Tutorgruppe 4 · Powerset practice',
    start_datetime: '2026-04-20T14:00:00Z',
    end_datetime: '2026-04-20T15:30:00Z',
  },
  {
    id: -2,
    course_name: 'Einführung in die Informatik',
    type: 'Lecture',
    name: 'EInf · Rekursion (H5 prep)',
    start_datetime: '2026-04-21T10:00:00Z',
    end_datetime: '2026-04-21T11:30:00Z',
  },
  {
    id: -3,
    course_name: 'Diskrete Strukturen',
    type: 'Lecture',
    name: 'DS · DFA minimization',
    start_datetime: '2026-04-22T09:30:00Z',
    end_datetime: '2026-04-22T11:00:00Z',
  },
  {
    id: -4,
    course_name: 'Analysis für Informatik',
    type: 'Lecture',
    name: 'Analysis · Potenzreihen',
    start_datetime: '2026-04-23T10:00:00Z',
    end_datetime: '2026-04-23T12:00:00Z',
  },
]

const MOCK_DEADLINES: OverviewDeadline[] = [
  {
    id: -1,
    course_name: 'Diskrete Strukturen',
    name: 'DS Übungsblatt 8',
    datetime: '2026-04-21T23:59:00Z',
  },
  {
    id: -2,
    course_name: 'Einführung in die Informatik',
    name: 'EInf H5 (Rekursion)',
    datetime: '2026-04-22T23:59:00Z',
  },
  {
    id: -3,
    course_name: 'Analysis für Informatik',
    name: 'Analysis Übungsblatt 5',
    datetime: '2026-04-24T23:59:00Z',
  },
]

const MOCK_QUIZ_STAT: OverviewQuizStat = {
  total_taken: 7,
  average_percent: 73,
}

function Sidebar({
  overview,
  activePage,
  isAuthenticated,
  username,
  onNavigate,
  onLogout,
}: {
  overview: OverviewResp | null
  activePage: Page
  isAuthenticated: boolean
  username: string
  onNavigate: (page: Page) => void
  onLogout: () => void
}) {
  const data = overview ?? buildFallbackOverview()
  const navItems: { page: Page; label: string; requiresAuth?: boolean }[] = [
    { page: 'recommended', label: 'Recommended', requiresAuth: true },
    { page: 'chat', label: 'Chat', requiresAuth: true },
    { page: 'demo', label: 'Demo chat', requiresAuth: true },
  ]

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-brand-mark">TT</span>
        <div style={{ flex: 1 }}>
          <p className="eyebrow">TumTum</p>
          <p className="sidebar-brand-sub">Study coach</p>
        </div>
        <GlowDot color={isAuthenticated ? 'success' : 'danger'} />
      </div>

      <SidebarSection title="Navigation">
        <div className="sidebar-nav">
          {navItems.map((item) => {
            const locked = item.requiresAuth && !isAuthenticated
            return (
              <button
                key={item.page}
                type="button"
                className={`sidebar-nav-link ${activePage === item.page ? 'is-active' : ''}`}
                onClick={() => onNavigate(locked ? 'login' : item.page)}
              >
                <span>{item.label}</span>
                {locked ? <Badge variant="muted">Auth</Badge> : null}
              </button>
            )
          })}
        </div>
      </SidebarSection>

      <SidebarSection title="Session">
        <div className="sidebar-session">
          <p className="sidebar-session-user">{isAuthenticated ? username || 'Signed in' : 'Guest'}</p>
          <p className="sidebar-session-copy">
            {isAuthenticated
              ? 'Recommendations and chat now use your backend token.'
              : 'Log in to load real events, deadlines, and saved chat history.'}
          </p>
          {isAuthenticated ? (
            <Button type="button" variant="ghost" size="sm" onClick={onLogout}>
              Sign out
            </Button>
          ) : null}
        </div>
      </SidebarSection>

      <SidebarSection title="Focus today">
        <div className="sidebar-stats">
          <div className="sidebar-stat">
            <span className="sidebar-stat-value">{MOCK_FOCUS_STATS.streak_days}d</span>
            <span className="sidebar-stat-label">streak</span>
          </div>
          <div className="sidebar-stat">
            <span className="sidebar-stat-value">{MOCK_FOCUS_STATS.minutes_today}m</span>
            <span className="sidebar-stat-label">of {MOCK_FOCUS_STATS.minutes_goal}m</span>
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
          {MOCK_STREAK_WEEK.map((day) => {
            const level = Math.min(4, Math.floor(day.minutes / 45))
            return (
              <div
                key={day.label}
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
          {MOCK_TOPIC_FOCUS.map((topic) => (
            <li key={topic.topic} className="sidebar-topic">
              <div className="sidebar-topic-head">
                <span className="sidebar-topic-title">{topic.topic}</span>
                <span className={`sidebar-topic-trend sidebar-topic-trend-${topic.trend}`}>
                  {topic.trend === 'up' ? '↑' : topic.trend === 'down' ? '↓' : '→'}
                </span>
              </div>
              <div className="sidebar-topic-meta">
                <span className="sidebar-topic-course">{topic.course}</span>
                <span className="sidebar-topic-pct">{topic.mastery}%</span>
              </div>
              <div className="sidebar-topic-bar" aria-hidden>
                <div className="sidebar-topic-bar-fill" style={{ width: `${topic.mastery}%` }} />
              </div>
            </li>
          ))}
        </ul>
      </SidebarSection>

      <SidebarSection title="Study plan">
        <ul className="sidebar-list sidebar-plan">
          {MOCK_STUDY_PLAN.map((slot) => (
            <li key={`${slot.time}-${slot.title}`} className={`sidebar-plan-item sidebar-plan-${slot.status}`}>
              <span className="sidebar-plan-time">{slot.time}</span>
              <span className="sidebar-plan-title">{slot.title}</span>
              <span className="sidebar-plan-badge">
                {slot.status === 'done' ? '✓' : slot.status === 'now' ? 'now' : ''}
              </span>
            </li>
          ))}
        </ul>
      </SidebarSection>

      <SidebarSection title="Courses">
        <ul className="sidebar-list">
          {data.courses.map((course) => (
            <li key={course.id} className="sidebar-item">
              <span className="sidebar-dot" aria-hidden />
              <span>{course.name}</span>
            </li>
          ))}
        </ul>
      </SidebarSection>

      <SidebarSection title="Recent wins">
        <ul className="sidebar-list sidebar-wins">
          {MOCK_RECENT_WINS.map((win) => (
            <li key={`${win.when}-${win.text}`} className="sidebar-win">
              <span className="sidebar-win-when">{win.when}</span>
              <span className="sidebar-win-text">{win.text}</span>
            </li>
          ))}
        </ul>
      </SidebarSection>

      <SidebarSection title="Upcoming events">
        <ul className="sidebar-list">
          {data.upcoming_events.map((event) => (
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
      </SidebarSection>

      <SidebarSection title="Deadlines">
        <ul className="sidebar-list">
          {data.upcoming_deadlines.map((deadline) => (
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
      </SidebarSection>

      <SidebarSection title="Quiz performance">
        <div className="sidebar-stats">
          <div className="sidebar-stat">
            <span className="sidebar-stat-value">{data.quiz.total_taken}</span>
            <span className="sidebar-stat-label">quizzes</span>
          </div>
          <div className="sidebar-stat">
            <span className="sidebar-stat-value">{data.quiz.average_percent}%</span>
            <span className="sidebar-stat-label">avg score</span>
          </div>
        </div>
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
  isLive?: boolean
  autoPlay?: boolean
  onClose: () => void
  onComplete: (answers: number[]) => void
}

function QuizOverlay({
  questions,
  title,
  isLive = false,
  autoPlay,
  onClose,
  onComplete,
}: QuizOverlayProps) {
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
      setIndex((currentIndex) => currentIndex + 1)
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
    setIndex((currentIndex) => currentIndex + 1)
  }

  return (
    <div className="quiz-backdrop" role="dialog" aria-modal="true">
      <div className="quiz-card">
        <header className="quiz-header">
          <div className="quiz-title-row">
            <p className="eyebrow">{title}</p>
            {isLive ? <Badge variant="success">LIVE · cognee</Badge> : <Badge variant="muted">scripted fallback</Badge>}
          </div>
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
              <strong>{selected === current.correct_index ? 'Correct.' : 'Not quite.'}</strong>{' '}
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

function pageFromHash(hash: string, isAuthenticated: boolean): Page {
  const raw = hash.replace(/^#/, '')
  if (raw === 'signup') return 'signup'
  if (raw === 'recommended') return isAuthenticated ? 'recommended' : 'login'
  if (raw === 'chat') return isAuthenticated ? 'chat' : 'login'
  if (raw === 'demo') return isAuthenticated ? 'demo' : 'login'
  return 'login'
}

function ensurePublicPage(page: Page): Page {
  if (page === 'recommended' || page === 'chat' || page === 'demo') {
    return 'login'
  }
  return page
}

function displayMessageContent(content: string) {
  return content.replace(
    /\s*\[(?:tokens:\s*prompt=|tokens=)[^\]]+\]\s*$/i,
    '',
  )
}

function buildFallbackOverview(): OverviewResp {
  return {
    now: new Date().toISOString(),
    courses: MOCK_COURSES,
    upcoming_events: MOCK_EVENTS,
    upcoming_deadlines: MOCK_DEADLINES,
    quiz: MOCK_QUIZ_STAT,
  }
}

function buildRecommendations(overview: OverviewResp | null): RecommendedItem[] {
  const data = overview ?? buildFallbackOverview()
  const events = data.upcoming_events.slice(0, 4).map((event) => {
    const hoursAway = hoursUntil(event.start_datetime)
    return {
      id: `event-${event.id}`,
      kind: 'event' as const,
      title: event.name,
      course: event.course_name || 'Course',
      primaryTime: formatDayTime(event.start_datetime),
      secondaryTime: `${formatShortTime(event.start_datetime)}–${formatShortTime(event.end_datetime)}`,
      badge: event.type,
      tone: hoursAway <= 18 ? ('success' as const) : ('accent' as const),
      reason:
        hoursAway <= 18
          ? 'Starting soon, so this is the easiest place to build momentum today.'
          : 'Coming up next in your schedule and worth planning around early.',
    }
  })

  const deadlines = data.upcoming_deadlines.slice(0, 3).map((deadline) => {
    const hoursAway = hoursUntil(deadline.datetime)
    return {
      id: `deadline-${deadline.id}`,
      kind: 'deadline' as const,
      title: deadline.name,
      course: deadline.course_name || 'Course',
      primaryTime: formatDayTime(deadline.datetime),
      badge: hoursAway <= 36 ? 'Due soon' : 'Deadline',
      tone: hoursAway <= 36 ? ('danger' as const) : ('accent' as const),
      reason:
        hoursAway <= 36
          ? 'This deadline is close enough that it should influence what you study next.'
          : 'A near-future deliverable that pairs well with the upcoming course events.',
    }
  })

  return [...deadlines, ...events].slice(0, 6)
}

function hoursUntil(iso: string) {
  return Math.round((new Date(iso).getTime() - Date.now()) / 3_600_000)
}

async function readError(response: Response, fallback: string) {
  try {
    const body = (await response.json()) as { detail?: string }
    return body.detail || fallback
  } catch {
    return fallback
  }
}

function formatTime(iso: string) {
  try {
    const date = new Date(iso)
    return date.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function formatShortTime(iso: string) {
  try {
    const date = new Date(iso)
    return date.toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function formatDayTime(iso: string) {
  try {
    const date = new Date(iso)
    return date.toLocaleString(undefined, {
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
