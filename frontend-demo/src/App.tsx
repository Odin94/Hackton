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

type Page = 'login' | 'signup' | 'recommended' | 'recreational' | 'quizzes' | 'chat' | 'demo'

type AuthResponse = {
  token: string
  user_id: number
  username: string
}

type ChatMessage = {
  id: number
  user_id: number
  timestamp: string
  author: 'user' | 'TumTum' | 'system'
  sequence_number: number
  content: string
  processing_ms?: number | null
}

type ChatReplyResponse = {
  user_message: ChatMessage
  assistant_message: ChatMessage
  demo_quiz?: {
    title: string
    topic: string
    questions: DemoQuizApiQuestion[]
    assistant_message: ChatMessage
  } | null
}

type DemoTriggerResponse = {
  notification_message?: ChatMessage | null
}

type DemoQuizApiQuestion = {
  question: string
  answer: string
  options: [string, string, string, string]
  correct_index: number
  topic: string
  source_ref: string | null
}

type DemoQuizCompleteResponse = {
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

type StoredQuizQuestion = {
  question: string
  answer: string
  options: [string, string, string, string]
  correct_index: number
  topic: string
  source_ref: string | null
}

type QuizLibraryItem = {
  id: number
  title: string
  topic: string
  course_name: string | null
  estimated_duration_minutes: number
  created_at: string
  question_count: number
  attempt_count: number
  average_percent: number | null
  best_percent: number | null
  latest_percent: number | null
  overall_average_percent: number
  underperformed: boolean
  questions: StoredQuizQuestion[]
}

type QuizLibraryResponse = {
  overall_average_percent: number
  quizzes: QuizLibraryItem[]
}

type RetakeQuizResponse = {
  quiz_id: number
  quiz_result_id: number
  latest_percent: number
  average_percent: number
  attempt_count: number
}

type QuizFlowMode = 'scripted-demo' | 'lecture-demo' | 'quiz-library'

type MessageQuizAttachment = {
  messageId: number
  title: string
  topic: string
  questions: DemoQuizQuestion[]
  isLive: boolean
  autoPlay: boolean
  mode: Exclude<QuizFlowMode, 'quiz-library'>
  status: 'ready' | 'completed'
}

type RecommendedItem = {
  id: string
  kind: 'event' | 'deadline'
  title: string
  course: string
  timestamp: string
  primaryTime: string
  secondaryTime?: string
  badge: string
  tone: 'accent' | 'success' | 'danger'
  reason: string
}

type RecommendedDayGroup = {
  id: string
  label: string
  deadlines: RecommendedItem[]
  events: RecommendedItem[]
}

type DiscoveredEvent = {
  id: number
  title: string
  description: string
  url: string | null
  location: string | null
  event_date: string | null
  signup_deadline: string | null
  category: string
  score: number
  score_reasoning: string | null
  notified: boolean
}

type EventListResponse = { events: DiscoveredEvent[] }

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
  recreational: 'Recommended recreational events',
  quizzes: 'Quiz library',
  chat: 'Chat',
  demo: 'Demo chat',
}

const CATEGORY_COLORS: Record<string, string> = {
  career: '#1a6b4a',
  networking: '#284d4b',
  fun: '#b85c00',
  other: '#5a4f45',
}

function typingDurationMs(text: string): number {
  return Math.max(600, text.length * TYPING_MS_PER_CHAR)
}

function logStatusError(message: string, error?: unknown) {
  if (error) {
    console.error(message, error)
    return
  }
  console.error(message)
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
  const [, setStatus] = useState(
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
  const [isTriggeringBackendDemo, setIsTriggeringBackendDemo] = useState(false)
  const [quizOpen, setQuizOpen] = useState(false)
  const [quizAutoPlay, setQuizAutoPlay] = useState(false)
  const [quizMode, setQuizMode] = useState<QuizFlowMode>('scripted-demo')
  const [overview, setOverview] = useState<OverviewResp | null>(null)
  const [quizLibrary, setQuizLibrary] = useState<QuizLibraryResponse | null>(null)
  const [isLoadingQuizLibrary, setIsLoadingQuizLibrary] = useState(false)
  const [quizLibraryError, setQuizLibraryError] = useState('')
  const [activeRetakeQuizId, setActiveRetakeQuizId] = useState<number | null>(null)
  const [discoveredEvents, setDiscoveredEvents] = useState<DiscoveredEvent[]>([])
  const [isLoadingDiscoveredEvents, setIsLoadingDiscoveredEvents] = useState(false)
  const [isScanningDiscoveredEvents] = useState(false)
  const [discoveredEventsError, setDiscoveredEventsError] = useState('')
  const [activeQuiz, setActiveQuiz] = useState<{
    title: string
    topic: string
    questions: DemoQuizQuestion[]
  }>(FALLBACK_QUIZ)
  const [activeQuizIsLive, setActiveQuizIsLive] = useState(false)
  const [activeQuizMessageId, setActiveQuizMessageId] = useState<number | null>(null)
  const [messageQuizzes, setMessageQuizzes] = useState<MessageQuizAttachment[]>([])
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
      setQuizLibrary(null)
      setQuizLibraryError('')
      setDiscoveredEvents([])
      setDiscoveredEventsError('')
      setMessages([])
      setMessageQuizzes([])
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
    if (!token && (page === 'recommended' || page === 'recreational' || page === 'quizzes' || page === 'chat' || page === 'demo')) {
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
      logStatusError(`Could not refresh recommendations: ${toMessage(err)}`, err)
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
    void loadQuizLibrary(token)
    void loadDiscoveredEvents(token)
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
      logStatusError('WebSocket error')
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
      logStatusError('Your saved session expired. Please log in again.', err)
      setStatus('Your saved session expired. Please log in again.')
    } finally {
      setIsLoadingHistory(false)
    }
  }

  async function loadDiscoveredEvents(activeToken = token) {
    if (!activeToken) {
      return
    }
    setIsLoadingDiscoveredEvents(true)
    setDiscoveredEventsError('')
    try {
      const response = await fetch('/events/', {
        headers: { Authorization: `Bearer ${activeToken}` },
      })
      if (!response.ok) {
        throw new Error(await readError(response, 'Could not load recreational events.'))
      }
      const body = (await response.json()) as EventListResponse
      setDiscoveredEvents(body.events)
    } catch (err) {
      setDiscoveredEventsError(toMessage(err))
    } finally {
      setIsLoadingDiscoveredEvents(false)
    }
  }

  async function loadQuizLibrary(activeToken = token) {
    if (!activeToken) {
      return
    }
    setIsLoadingQuizLibrary(true)
    setQuizLibraryError('')
    try {
      const response = await fetch('/demo/quizzes', {
        headers: { Authorization: `Bearer ${activeToken}` },
      })
      if (!response.ok) {
        throw new Error(await readError(response, 'Could not load quizzes.'))
      }
      const body = (await response.json()) as QuizLibraryResponse
      setQuizLibrary(body)
    } catch (err) {
      setQuizLibraryError(toMessage(err))
    } finally {
      setIsLoadingQuizLibrary(false)
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
      logStatusError('Could not log in.', err)
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
      logStatusError('Could not sign up.', err)
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

  function attachQuizToMessage(attachment: MessageQuizAttachment) {
    setMessageQuizzes((current) => {
      const next = current.filter((item) => item.messageId !== attachment.messageId)
      next.push(attachment)
      return next
    })
  }

  function openMessageQuiz(attachment: MessageQuizAttachment) {
    setActiveQuizMessageId(attachment.messageId)
    setActiveQuiz({
      title: attachment.title,
      topic: attachment.topic,
      questions: attachment.questions,
    })
    setActiveQuizIsLive(attachment.isLive)
    setQuizMode(attachment.mode)
    setQuizAutoPlay(attachment.autoPlay)
    setQuizOpen(true)
  }

  function renderMessageQuizAction(messageId: number) {
    const attachment = messageQuizzes.find((item) => item.messageId === messageId)
    if (!attachment) {
      return null
    }
    return (
      <div className="message-quiz-action">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => openMessageQuiz(attachment)}
          disabled={attachment.status === 'completed'}
        >
          {attachment.status === 'completed' ? 'Quiz completed' : 'Open quiz'}
        </Button>
        <span className="message-quiz-caption">
          {attachment.questions.length} questions
        </span>
      </div>
    )
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
      let quizMessageId: number | null = null

      if (liveBody && liveBody.assistant_message?.content) {
        quizMessageId = liveBody.assistant_message.id
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
        quizMessageId = fallbackReply.id
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
      setStatus(
        resolvedQuiz
          ? 'Scene D · LIVE cognee quiz — powerset drill'
          : 'Scene D · scripted fallback — cognee unreachable',
      )
      if (quizMessageId != null) {
        attachQuizToMessage({
          messageId: quizMessageId,
          title: nextQuiz.title,
          topic: nextQuiz.topic,
          questions: nextQuiz.questions,
          isLive: resolvedQuiz != null,
          autoPlay: true,
          mode: 'scripted-demo',
          status: 'ready',
        })
      }
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
      logStatusError(`Demo run failed: ${toMessage(err)}`, err)
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
      if (body.demo_quiz) {
        attachQuizToMessage({
          messageId: body.assistant_message.id,
          title: body.demo_quiz.title,
          topic: body.demo_quiz.topic,
          questions: body.demo_quiz.questions.map((question) => ({
            question: question.question,
            options: question.options,
            correct_index: question.correct_index,
            explanation: question.answer || 'Grounded in the course materials.',
            source_ref: question.source_ref ?? 'Lecture materials',
          })),
          isLive: true,
          autoPlay: false,
          mode: 'lecture-demo',
          status: 'ready',
        })
        setStatus('Lecture quiz ready.')
      } else {
        setStatus('Live LLM reply delivered.')
      }
    } catch (err) {
      setMessages((current) => current.filter((message) => message.id !== tempUserMessage.id))
      setDraft(outgoing)
      logStatusError(`Send failed: ${toMessage(err)}`, err)
      setStatus(`Send failed: ${toMessage(err)}`)
    } finally {
      setIsSending(false)
      setPendingUserMessageId(null)
    }
  }

  async function handleDemoTrigger() {
    if (!token || isTriggeringBackendDemo) {
      return
    }
    setIsTriggeringBackendDemo(true)
    setStatus('Simulating lecture ending...')
    try {
      const response = await fetch('/chat/demo-trigger', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ course_name: 'Machine Learning' }),
      })
      if (!response.ok) {
        throw new Error(await readError(response, 'Could not start the backend demo.'))
      }
      const body = (await response.json()) as DemoTriggerResponse
      if (body.notification_message) {
        setMessages((current) =>
          current.some((message) => message.id === body.notification_message!.id)
            ? current
            : [...current, body.notification_message!],
        )
      }
      setStatus('Lecture prompt delivered.')
    } catch (err) {
      logStatusError(`Could not start the backend demo: ${toMessage(err)}`, err)
      setStatus(`Could not start the backend demo: ${toMessage(err)}`)
    } finally {
      setIsTriggeringBackendDemo(false)
    }
  }

  function handleRetakeQuiz(quiz: QuizLibraryItem) {
    setActiveRetakeQuizId(quiz.id)
    setActiveQuiz({
      title: quiz.title,
      topic: quiz.topic,
      questions: quiz.questions.map((question) => ({
        question: question.question,
        options: question.options,
        correct_index: question.correct_index,
        explanation: question.answer || 'Grounded in the course materials.',
        source_ref: question.source_ref ?? 'Lecture materials',
      })),
    })
    setActiveQuizIsLive(true)
    setActiveQuizMessageId(null)
    setQuizMode('quiz-library')
    setQuizAutoPlay(false)
    setQuizOpen(true)
    setStatus(`Retaking ${quiz.title}...`)
  }

  const handleQuizComplete = useCallback(
    async (answers: number[]) => {
      setQuizOpen(false)
      setQuizAutoPlay(false)
      if (activeQuizMessageId != null) {
        setMessageQuizzes((current) =>
          current.map((item) =>
            item.messageId === activeQuizMessageId ? { ...item, status: 'completed' } : item,
          ),
        )
      }
      setActiveQuizMessageId(null)
      const correctCount = answers.reduce(
        (acc, choice, idx) =>
          acc + (choice === activeQuiz.questions[idx].correct_index ? 1 : 0),
        0,
      )
      if (quizMode === 'lecture-demo') {
        try {
          const response = await fetch('/chat/demo-quiz/complete', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({
              correct_answers: correctCount,
              false_answers: answers.length - correctCount,
            }),
          })
          if (!response.ok) {
            throw new Error(await readError(response, 'Could not save the lecture quiz.'))
          }
          const body = (await response.json()) as DemoQuizCompleteResponse
          setMessages((current) =>
            current.some((message) => message.id === body.assistant_message.id)
              ? current
              : [...current, body.assistant_message],
          )
          setStatus(`Lecture quiz saved: ${correctCount}/${answers.length}`)
          void Promise.all([refreshOverview(), loadQuizLibrary(token)])
        } catch (err) {
          logStatusError(`Lecture quiz save failed: ${toMessage(err)}`, err)
          setStatus(`Lecture quiz save failed: ${toMessage(err)}`)
        }
        return
      }
      if (quizMode === 'quiz-library') {
        if (activeRetakeQuizId == null || !token) {
          setStatus('Could not save the retake result.')
          return
        }
        try {
          const response = await fetch(`/demo/quizzes/${activeRetakeQuizId}/retake`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({
              correct_answers: correctCount,
              false_answers: answers.length - correctCount,
            }),
          })
          if (!response.ok) {
            throw new Error(await readError(response, 'Could not save the retake result.'))
          }
          await response.json() as Promise<RetakeQuizResponse>
          setStatus(`Retake saved: ${correctCount}/${answers.length}`)
          void Promise.all([refreshOverview(), loadQuizLibrary(token)])
        } catch (err) {
          logStatusError(`Retake save failed: ${toMessage(err)}`, err)
          setStatus(`Retake save failed: ${toMessage(err)}`)
        } finally {
          setActiveRetakeQuizId(null)
        }
        return
      }
      try {
        await saveQuizResults(activeQuiz, correctCount, answers.length)
        setStatus(`Quiz saved: ${correctCount}/${answers.length}`)
        void Promise.all([refreshOverview(), loadQuizLibrary(token)])
      } catch (err) {
        logStatusError(`Quiz save failed: ${toMessage(err)}`, err)
        setStatus(`Quiz save failed: ${toMessage(err)}`)
      }
      await sleep(700)
      try {
        await sendSystemMessage(
          `${SCENE_E_RECAP_PREFIX}${correctCount}/${answers.length} on the powerset drill.${SCENE_E_RECAP_BODY}`,
        )
      } catch (err) {
        logStatusError(`Scene E recap failed: ${toMessage(err)}`, err)
        setStatus(`Scene E recap failed: ${toMessage(err)}`)
      }
      const resolver = quizDoneResolverRef.current
      quizDoneResolverRef.current = null
      resolver?.()
    },
    [activeQuiz, activeQuizMessageId, activeRetakeQuizId, quizMode, refreshOverview, token],
  )

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
          isAuthenticated={Boolean(token)}
          isBusy={isAutoRunning}
          isTriggeringBackendDemo={isTriggeringBackendDemo}
          onPlayDemo={() => void runFullDemo()}
          onTriggerBackendDemo={() => void handleDemoTrigger()}
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
            isAuthenticated={Boolean(token)}
            isLoading={isLoadingOverview}
            overview={overview}
            onNavigate={setPage}
          />
        ) : null}

        {page === 'recreational' ? (
          <RecreationalEventsPage
            isAuthenticated={Boolean(token)}
            discoveredEvents={discoveredEvents}
            isLoadingDiscoveredEvents={isLoadingDiscoveredEvents}
            isScanningDiscoveredEvents={isScanningDiscoveredEvents}
            discoveredEventsError={discoveredEventsError}
            onNavigate={setPage}
          />
        ) : null}

        {page === 'quizzes' ? (
          <QuizLibraryPage
            isAuthenticated={Boolean(token)}
            quizLibrary={quizLibrary}
            isLoading={isLoadingQuizLibrary}
            error={quizLibraryError}
            onRetake={handleRetakeQuiz}
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
                    {renderMessageQuizAction(message.id)}
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
                rows={1}
                disabled={!token || isSending}
              />
              <div className="composer-actions">
                <p></p>
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
                    {renderMessageQuizAction(message.id)}
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
                  if (event.key !== 'Enter') return
                  if (event.metaKey || event.ctrlKey) return
                  event.preventDefault()
                  void handleSend(event as unknown as FormEvent<HTMLFormElement>)
                }}
                placeholder="Write a message..."
                rows={1}
                disabled={!token || isSending || isAutoRunning}
              />
              <div className="composer-actions">
                <p className="helper-text">
                  Use Run backend demo for the guided backend flow, or ▶ Play demo for the full scripted 7-scene run.
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
            setActiveQuizMessageId(null)
            setActiveRetakeQuizId(null)
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
  isAuthenticated,
  isBusy,
  isTriggeringBackendDemo,
  onPlayDemo,
  onTriggerBackendDemo,
  onNavigate,
}: {
  page: Page
  username: string
  isAuthenticated: boolean
  isBusy: boolean
  isTriggeringBackendDemo: boolean
  onPlayDemo: () => void
  onTriggerBackendDemo: () => void
  onNavigate: (page: Page) => void
}) {
  return (
    <header className="demo-header">
      <div>
        <p className="eyebrow">TumTum · Study Coach</p>
        <h1>{PAGE_LABELS[page]}</h1>
      </div>
      <div className="status-pill" aria-live="polite">
        {(page === 'chat' || page === 'demo') && isAuthenticated ? (
          <button
            type="button"
            className="play-button"
            onClick={onTriggerBackendDemo}
            disabled={isTriggeringBackendDemo}
          >
            {isTriggeringBackendDemo ? 'Simulate Lecture Ending' : 'Simulate Lecture Ending'}
          </button>
        ) : null}
        {page === 'demo' && isAuthenticated ? (
          <button type="button" className="play-button" onClick={onPlayDemo} disabled={isBusy}>
            {isBusy ? '▸ Running demo...' : '▶ Play demo'}
          </button>
        ) : null}
        {page === 'recommended' && isAuthenticated ? (
          <button type="button" className="play-button" onClick={() => onNavigate('recreational')}>
            Open recreational
          </button>
        ) : null}
        {page === 'recreational' && isAuthenticated ? (
          <button type="button" className="play-button" onClick={() => onNavigate('chat')}>
            Open chat
          </button>
        ) : null}
        {page === 'quizzes' && isAuthenticated ? (
          <button type="button" className="play-button" onClick={() => onNavigate('chat')}>
            Open chat
          </button>
        ) : null}
        <span className="status-text">{isAuthenticated ? username || 'Signed in' : ''}</span>
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
  isAuthenticated,
  isLoading,
  overview,
  onNavigate,
}: {
  isAuthenticated: boolean
  isLoading: boolean
  overview: OverviewResp | null
  onNavigate: (page: Page) => void
}) {
  const data = overview ?? buildFallbackOverview()
  const nextEvent = data.upcoming_events[0]
  const nextDeadline = data.upcoming_deadlines[0]
  const dayGroups = buildRecommendedDayGroups(data)

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
          <h2>{'Your next best moves'}</h2>
        </div>
        <div className="recommended-actions">
          {isLoading ? <Badge variant="muted">Refreshing...</Badge> : null}
          <Button type="button" variant="ghost" onClick={() => onNavigate('recreational')}>
            Events
          </Button>
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

      <div className="recommended-days">
        {dayGroups.map((group) => (
          <section key={group.id} className="recommended-day-section">
            <div className="recommended-day-header">
              <div>
                <p className="eyebrow">Day view</p>
                <h3>{group.label}</h3>
              </div>
            </div>

            <div className="recommended-day-row">
              <div className="recommended-day-lane">
                <div className="recommended-lane-header">
                  <h4>Deadlines</h4>
                </div>
                {group.deadlines.length ? (
                  <div className="recommended-items-grid">
                    {group.deadlines.map((item) => (
                      <RecommendedItemCard key={item.id} item={item} />
                    ))}
                  </div>
                ) : (
                  <p className="recommended-empty-copy">-</p>
                )}
              </div>

              <div className="recommended-day-lane">
                <div className="recommended-lane-header">
                  <h4>Events</h4>
                </div>
                {group.events.length ? (
                  <div className="recommended-items-grid">
                    {group.events.map((item) => (
                      <RecommendedItemCard key={item.id} item={item} />
                    ))}
                  </div>
                ) : (
                  <p className="recommended-empty-copy">-</p>
                )}
              </div>
            </div>
          </section>
        ))}
      </div>

    </section>
  )
}

function RecreationalEventsPage({
  isAuthenticated,
  discoveredEvents,
  isLoadingDiscoveredEvents,
  isScanningDiscoveredEvents,
  discoveredEventsError,
  onNavigate,
}: {
  isAuthenticated: boolean
  discoveredEvents: DiscoveredEvent[]
  isLoadingDiscoveredEvents: boolean
  isScanningDiscoveredEvents: boolean
  discoveredEventsError: string
  onNavigate: (page: Page) => void
}) {
  if (!isAuthenticated) {
    return (
      <section className="auth-page">
        <Card className="auth-card auth-card-highlight">
          <CardHeader
            title="Recommended recreational events"
            subtitle="Log in to load the personalized event discovery feed from the backend."
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
        <h3>Tailored event recommendations for April/May</h3>
      <section className="recommended-day-section recreational-events-section">
        {isLoadingDiscoveredEvents || isScanningDiscoveredEvents ? (
          <Card className="recommended-empty-card recreational-state-card">
            <CardContent>
              <p className="recommended-empty-copy">
                {isScanningDiscoveredEvents
                  ? 'Scanning for fresh recreational events...'
                  : 'Loading saved recreational events...'}
              </p>
            </CardContent>
          </Card>
        ) : discoveredEventsError ? (
          <Card className="recommended-empty-card recreational-state-card">
            <CardContent>
              <p className="recommended-empty-copy">{discoveredEventsError}</p>
            </CardContent>
          </Card>
        ) : discoveredEvents.length ? (
          <div className="events-grid recreational-events-grid">
            {discoveredEvents.map((event) => (
              <DiscoveredEventCard key={event.id} event={event} />
            ))}
          </div>
        ) : (
          <Card className="recommended-empty-card recreational-state-card">
            <CardContent>
              <p className="recommended-empty-copy">
                No recreational recommendations yet. Run a scan to fetch tailored picks.
              </p>
            </CardContent>
          </Card>
        )}
      </section>
    </section>
  )
}

function QuizLibraryPage({
  isAuthenticated,
  quizLibrary,
  isLoading,
  error,
  onRetake,
  onNavigate,
}: {
  isAuthenticated: boolean
  quizLibrary: QuizLibraryResponse | null
  isLoading: boolean
  error: string
  onRetake: (quiz: QuizLibraryItem) => void
  onNavigate: (page: Page) => void
}) {
  if (!isAuthenticated) {
    return (
      <section className="auth-page">
        <Card className="auth-card auth-card-highlight">
          <CardHeader
            title="Quiz library"
            subtitle="Log in to review your saved quizzes, weak spots, and retake them."
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

  const overallAverage = quizLibrary?.overall_average_percent ?? 0
  const quizzes = quizLibrary?.quizzes ?? []
  const underperformingCount = quizzes.filter((quiz) => quiz.underperformed).length

  return (
    <section className="recommended-page">
      <div className="recommended-hero">
        <div>
          <p className="eyebrow">Quiz library</p>
          <h2>Retake what needs another pass</h2>
        </div>
        <div className="recommended-actions">
          <Badge variant={underperformingCount > 0 ? 'danger' : 'success'}>
            {underperformingCount > 0
              ? `${underperformingCount} weak spot${underperformingCount === 1 ? '' : 's'}`
              : 'On track'}
          </Badge>
          <Button type="button" variant="ghost" onClick={() => onNavigate('recommended')}>
            Overview
          </Button>
          <Button type="button" onClick={() => onNavigate('chat')}>
            Open chat
          </Button>
        </div>
      </div>

      <div className="recommended-summary-grid">
        <Card>
          <CardHeader title="Overall average" subtitle="Across all completed quiz attempts" />
          <CardContent>
            <p className="summary-number">{overallAverage}%</p>
            <p className="summary-caption">Used as the baseline for weak-spot highlighting.</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader title="Saved quizzes" subtitle="Every grounded quiz stored for this user" />
          <CardContent>
            <p className="summary-number">{quizzes.length}</p>
            <p className="summary-caption">
              {quizzes.length ? `${quizzes.reduce((sum, quiz) => sum + quiz.attempt_count, 0)} attempts logged` : 'No quizzes yet'}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader title="Underperformed" subtitle="Average below your overall average" />
          <CardContent>
            <p className="summary-number">{underperformingCount}</p>
            <p className="summary-caption">Highlighted first so you know what to revisit.</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader title="Ready to retake" subtitle="Stored question sets reopen in the popup" />
          <CardContent>
            <p className="summary-number">{quizzes.filter((quiz) => quiz.question_count > 0).length}</p>
            <p className="summary-caption">Every card below can be retaken with one click.</p>
          </CardContent>
        </Card>
      </div>

      {isLoading ? (
        <Card className="recommended-empty-card">
          <CardContent>
            <p className="recommended-empty-copy">Loading your quiz library...</p>
          </CardContent>
        </Card>
      ) : error ? (
        <Card className="recommended-empty-card">
          <CardContent>
            <p className="recommended-empty-copy">{error}</p>
          </CardContent>
        </Card>
      ) : quizzes.length === 0 ? (
        <Card className="recommended-empty-card">
          <CardContent>
            <p className="recommended-empty-copy">
              No quizzes recorded yet. Run a demo flow or finish a drill to populate this page.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="quiz-library-grid">
          {quizzes
            .slice()
            .sort((left, right) => Number(right.underperformed) - Number(left.underperformed))
            .map((quiz) => (
              <Card
                key={quiz.id}
                className={`quiz-library-card ${quiz.underperformed ? 'is-underperforming' : ''}`}
              >
                <CardHeader
                  title={quiz.title}
                  subtitle={quiz.course_name ?? quiz.topic}
                  action={
                    <Badge variant={quiz.underperformed ? 'danger' : 'success'}>
                      {quiz.underperformed ? 'Needs work' : 'Healthy'}
                    </Badge>
                  }
                />
                <CardContent className="quiz-library-body">
                  <div className="quiz-library-stats">
                    <div className="quiz-library-stat">
                      <span className="quiz-library-stat-label">Average</span>
                      <strong>{quiz.average_percent != null ? `${quiz.average_percent}%` : 'Not taken'}</strong>
                    </div>
                    <div className="quiz-library-stat">
                      <span className="quiz-library-stat-label">Latest</span>
                      <strong>{quiz.latest_percent != null ? `${quiz.latest_percent}%` : 'No score'}</strong>
                    </div>
                    <div className="quiz-library-stat">
                      <span className="quiz-library-stat-label">Best</span>
                      <strong>{quiz.best_percent != null ? `${quiz.best_percent}%` : 'No score'}</strong>
                    </div>
                    <div className="quiz-library-stat">
                      <span className="quiz-library-stat-label">Attempts</span>
                      <strong>{quiz.attempt_count}</strong>
                    </div>
                  </div>
                  <p className="quiz-library-meta">
                    {quiz.question_count} questions · about {quiz.estimated_duration_minutes} min · created{' '}
                    {formatDayTime(quiz.created_at)}
                  </p>
                  <p className="quiz-library-note">
                    {quiz.underperformed
                      ? `Below your ${quiz.overall_average_percent}% overall average. Worth another pass.`
                      : `At or above your ${quiz.overall_average_percent}% overall average.`}
                  </p>
                  <div className="quiz-library-actions">
                    <Button type="button" onClick={() => onRetake(quiz)}>
                      Retake quiz
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
        </div>
      )}
    </section>
  )
}

function RecommendedItemCard({ item }: { item: RecommendedItem }) {
  return (
    <Card className="recommended-card">
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
  )
}

function CategoryBadge({ category }: { category: string }) {
  return (
    <span
      className="category-badge"
      style={{ background: CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other }}
    >
      {category}
    </span>
  )
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, score))
  const hue = Math.round((pct / 100) * 120)
  return (
    <div className="score-bar-track" title={`Score: ${pct}/100`}>
      <div
        className="score-bar-fill"
        style={{ width: `${pct}%`, background: `hsl(${hue}, 60%, 38%)` }}
      />
      <span className="score-bar-label">{pct}/100</span>
    </div>
  )
}

function DiscoveredEventCard({ event }: { event: DiscoveredEvent }) {
  return (
    <article className="event-card">
      <div className="event-card-header">
        <CategoryBadge category={event.category} />
        {event.notified ? <Badge variant="muted">Notified</Badge> : null}
      </div>
      <h3 className="event-card-title">{event.title}</h3>
      <ScoreBar score={event.score} />
      {event.score_reasoning ? <p className="event-reasoning">{event.score_reasoning}</p> : null}
      <div className="event-meta">
        {event.event_date ? <span className="event-meta-item">📅 {event.event_date}</span> : null}
        {event.location ? <span className="event-meta-item">📍 {event.location}</span> : <span />}
        {event.signup_deadline ? (
          <span className="event-meta-item">Signup by {event.signup_deadline}</span>
        ) : null}
      </div>
      <p className="event-description">
        {event.description.slice(0, 220)}
        {event.description.length > 220 ? '…' : ''}
      </p>
      {event.url ? (
        <a className="event-link" href={event.url} target="_blank" rel="noopener noreferrer">
          View event →
        </a>
      ) : null}
    </article>
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

const MOCK_SEMESTER_PROGRESS = {
  exams_percent: 68,
  exams_label: '5 weeks left',
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

function clampPercent(value: number) {
  return Math.max(0, Math.min(100, Math.round(value)))
}

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
  const studyProgressPercent = clampPercent(Math.max(data.quiz.average_percent, 12))
  const examProgressPercent = clampPercent(MOCK_SEMESTER_PROGRESS.exams_percent)
  const onTrackDelta = studyProgressPercent - examProgressPercent
  const onTrackLabel =
    onTrackDelta >= 8 ? 'Ahead' : onTrackDelta >= -6 ? 'On track' : 'Behind'
  const onTrackVariant =
    onTrackDelta >= 8 ? 'success' : onTrackDelta >= -6 ? 'accent' : 'danger'
  const navItems: { page: Page; label: string; requiresAuth?: boolean }[] = [
    { page: 'recommended', label: 'TumTum', requiresAuth: true },
    { page: 'recreational', label: 'Events', requiresAuth: true },
    { page: 'quizzes', label: 'Quizzes', requiresAuth: true },
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
        <div className="sidebar-progress-stack">
          <div className="sidebar-progress-headline">
            <span className="sidebar-progress-caption">Semester pace</span>
            <Badge variant={onTrackVariant}>{onTrackLabel}</Badge>
          </div>
          <div className="sidebar-progress-metric">
            <div className="sidebar-progress-meta">
              <span>Until exams</span>
              <span>{MOCK_SEMESTER_PROGRESS.exams_label}</span>
            </div>
            <div
              className="sidebar-progress"
              role="progressbar"
              aria-label="Until exams"
              aria-valuenow={examProgressPercent}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="sidebar-progress-fill sidebar-progress-fill-timeline"
                style={{ width: `${examProgressPercent}%` }}
              />
            </div>
          </div>
          <div className="sidebar-progress-metric">
            <div className="sidebar-progress-meta">
              <span>Studying progress</span>
              <span>{studyProgressPercent}% ready</span>
            </div>
            <div
              className="sidebar-progress"
              role="progressbar"
              aria-label="Studying progress"
              aria-valuenow={studyProgressPercent}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="sidebar-progress-fill sidebar-progress-fill-study"
                style={{ width: `${studyProgressPercent}%` }}
              />
            </div>
          </div>
          <p className="sidebar-progress-note">
            {onTrackDelta >= 0
              ? `${onTrackDelta}% above the semester pace based on recent quiz performance.`
              : `${Math.abs(onTrackDelta)}% below the semester pace. Time for a retake block.`}
          </p>
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
  if (raw === 'recreational') return isAuthenticated ? 'recreational' : 'login'
  if (raw === 'quizzes') return isAuthenticated ? 'quizzes' : 'login'
  if (raw === 'chat') return isAuthenticated ? 'chat' : 'login'
  if (raw === 'demo') return isAuthenticated ? 'demo' : 'login'
  return 'login'
}

function ensurePublicPage(page: Page): Page {
  if (page === 'recommended' || page === 'recreational' || page === 'quizzes' || page === 'chat' || page === 'demo') {
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

function buildRecommendedDayGroups(data: OverviewResp): RecommendedDayGroup[] {
  const groups = new Map<string, RecommendedDayGroup>()

  const events = data.upcoming_events.map((event) => {
    const hoursAway = hoursUntil(event.start_datetime)
    return {
      id: `event-${event.id}`,
      kind: 'event' as const,
      title: event.name,
      course: event.course_name || 'Course',
      timestamp: event.start_datetime,
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

  const deadlines = data.upcoming_deadlines.map((deadline) => {
    const hoursAway = hoursUntil(deadline.datetime)
    return {
      id: `deadline-${deadline.id}`,
      kind: 'deadline' as const,
      title: deadline.name,
      course: deadline.course_name || 'Course',
      timestamp: deadline.datetime,
      primaryTime: formatDayTime(deadline.datetime),
      badge: hoursAway <= 36 ? 'Due soon' : 'Deadline',
      tone: hoursAway <= 36 ? ('danger' as const) : ('accent' as const),
      reason:
        hoursAway <= 36
          ? 'This deadline is close enough that it should influence what you study next.'
          : 'A near-future deliverable that pairs well with the upcoming course events.',
    }
  })

  for (const item of [...deadlines, ...events]) {
    const day = describeCalendarDay(item.timestamp)
    const existing = groups.get(day.id)
    if (existing) {
      if (item.kind === 'deadline') {
        existing.deadlines.push(item)
      } else {
        existing.events.push(item)
      }
      continue
    }
    groups.set(day.id, {
      id: day.id,
      label: day.label,
      deadlines: item.kind === 'deadline' ? [item] : [],
      events: item.kind === 'event' ? [item] : [],
    })
  }

  return [...groups.values()]
    .sort((left, right) => {
      const leftTime = Math.min(
        ...left.deadlines.concat(left.events).map((item) => new Date(item.timestamp).getTime()),
      )
      const rightTime = Math.min(
        ...right.deadlines.concat(right.events).map((item) => new Date(item.timestamp).getTime()),
      )
      return leftTime - rightTime
    })
    .map((group) => ({
      ...group,
      deadlines: [...group.deadlines].sort(
        (left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime(),
      ),
      events: [...group.events].sort(
        (left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime(),
      ),
    }))
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

function describeCalendarDay(iso: string) {
  const date = new Date(iso)
  return {
    id: date.toISOString().slice(0, 10),
    label: date.toLocaleDateString(undefined, {
      weekday: 'long',
      month: 'short',
      day: 'numeric',
    }),
  }
}

function toMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }
  return 'Unexpected error.'
}

export default App
