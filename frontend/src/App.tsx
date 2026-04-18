import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'

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

type DemoTriggerResponse = {
  notification_message?: ChatMessage | null
}

type ChatSocketPayload = {
  type: 'chat_message' | 'ack'
  message?: ChatMessage
  echo?: string
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

type EventListResponse = {
  events: DiscoveredEvent[]
}

type DiscoverResponse = {
  total_events_found: number
  top_events: DiscoveredEvent[]
}

const TOKEN_KEY = 'hackton-chat-token'
const USERNAME_KEY = 'hackton-chat-username'
const DEMO_COURSE_NAME = 'Machine Learning'

const CATEGORY_COLORS: Record<string, string> = {
  career: '#1a6b4a',
  networking: '#284d4b',
  fun: '#b85c00',
  other: '#5a4f45',
}

function CategoryBadge({ category }: { category: string }) {
  const color = CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other
  return (
    <span className="category-badge" style={{ background: color }}>
      {category}
    </span>
  )
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, score))
  const hue = Math.round((pct / 100) * 120) // 0 = red, 120 = green
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

function EventCard({ event }: { event: DiscoveredEvent }) {
  return (
    <article className="event-card">
      <div className="event-card-header">
        <CategoryBadge category={event.category} />
        {event.notified && <span className="notified-badge">Notified</span>}
      </div>
      <h3 className="event-card-title">{event.title}</h3>
      <ScoreBar score={event.score} />
      {event.score_reasoning && (
        <p className="event-reasoning">{event.score_reasoning}</p>
      )}
      <div className="event-meta">
        {event.event_date && (
          <span className="event-meta-item">📅 {event.event_date}</span>
        )}
        {event.location && (
          <span className="event-meta-item">📍 {event.location}</span>
        )}
        {event.signup_deadline && (
          <span className="event-meta-item">⏰ Signup by {event.signup_deadline}</span>
        )}
      </div>
      <p className="event-description">{event.description.slice(0, 220)}{event.description.length > 220 ? '…' : ''}</p>
      {event.url && (
        <a
          className="event-link"
          href={event.url}
          target="_blank"
          rel="noopener noreferrer"
        >
          View event →
        </a>
      )}
    </article>
  )
}

function App() {
  const [username, setUsername] = useState(() => localStorage.getItem(USERNAME_KEY) ?? '')
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? '')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('Log in with an existing username to load your chat history.')
  const [isLoggingIn, setIsLoggingIn] = useState(false)
  const [signupName, setSignupName] = useState('')
  const [isSigningUp, setIsSigningUp] = useState(false)
  const [isLoadingHistory, setIsLoadingHistory] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [isTriggeringDemo, setIsTriggeringDemo] = useState(false)
  const listRef = useRef<HTMLDivElement | null>(null)

  // Events view state
  const [activeView, setActiveView] = useState<'chat' | 'events'>('chat')
  const [events, setEvents] = useState<DiscoveredEvent[]>([])
  const [isScanning, setIsScanning] = useState(false)
  const [isLoadingEvents, setIsLoadingEvents] = useState(false)
  const [eventsLoaded, setEventsLoaded] = useState(false)
  const [scanCount, setScanCount] = useState<number | null>(null)

  useEffect(() => {
    localStorage.setItem(USERNAME_KEY, username)
  }, [username])

  useEffect(() => {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token)
      void loadHistory(token)
      return
    }

    localStorage.removeItem(TOKEN_KEY)
    setMessages([])
  }, [token])

  useEffect(() => {
    const list = listRef.current
    if (!list) return
    list.scrollTop = list.scrollHeight
  }, [messages])

  useEffect(() => {
    if (!token) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socketUrl = `${protocol}//${window.location.host}/ws?token=${encodeURIComponent(token)}`
    const socket = new WebSocket(socketUrl)

    socket.addEventListener('open', () => {
      setStatus((current) =>
        current === 'Thinking...' ? current : 'Live chat connection ready.',
      )
    })

    socket.addEventListener('message', (event) => {
      const payload = JSON.parse(event.data) as ChatSocketPayload
      if (payload.type !== 'chat_message' || !payload.message) return
      const incomingMessage = payload.message

      setMessages((current) => {
        if (current.some((message) => message.id === incomingMessage.id)) return current
        return [...current, incomingMessage]
      })
      setStatus('A scheduled notification arrived in chat.')
    })

    socket.addEventListener('close', () => {
      setStatus((current) =>
        token && current !== 'Logged out. Log in again to load chat history.'
          ? 'Live chat connection closed.'
          : current,
      )
    })

    socket.addEventListener('error', () => {
      setError('WebSocket connection failed.')
    })

    return () => { socket.close() }
  }, [token])

  // Load saved events when switching to events view
  useEffect(() => {
    if (activeView === 'events' && token && !eventsLoaded) {
      void loadEvents()
    }
  }, [activeView, token])

  async function loadHistory(activeToken: string) {
    setIsLoadingHistory(true)
    setError('')

    try {
      const response = await fetch('/chat/history', {
        headers: { Authorization: `Bearer ${activeToken}` },
      })

      if (!response.ok) throw new Error(await readError(response, 'Could not load chat history.'))

      const body = (await response.json()) as { messages: ChatMessage[] }
      setMessages(body.messages)
      setStatus(
        body.messages.length
          ? 'History loaded from SQLite.'
          : 'No chat history yet. Ask a question to create the first turn.',
      )
    } catch (err) {
      setMessages([])
      setToken('')
      setError(toMessage(err))
      setStatus('Login token was cleared. Please log in again.')
    } finally {
      setIsLoadingHistory(false)
    }
  }

  async function loadEvents() {
    if (!token) return
    setIsLoadingEvents(true)
    try {
      const response = await fetch('/events/', {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) throw new Error(await readError(response, 'Could not load events.'))
      const body = (await response.json()) as EventListResponse
      setEvents(body.events)
      setEventsLoaded(true)
    } catch (err) {
      setError(toMessage(err))
    } finally {
      setIsLoadingEvents(false)
    }
  }

  async function handleScanEvents() {
    if (!token) {
      setError('Log in before scanning for events.')
      return
    }

    setIsScanning(true)
    setError('')
    setScanCount(null)

    try {
      const response = await fetch('/events/discover', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) throw new Error(await readError(response, 'Event scan failed.'))
      const body = (await response.json()) as DiscoverResponse
      setScanCount(body.total_events_found)
      // Reload the full list after scan
      setEventsLoaded(false)
      await loadEvents()
    } catch (err) {
      setError(toMessage(err))
    } finally {
      setIsScanning(false)
    }
  }

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!username.trim()) {
      setError('Enter a username first.')
      return
    }

    setIsLoggingIn(true)
    setError('')
    setStatus('Logging in...')

    try {
      const response = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim() }),
      })

      if (!response.ok) throw new Error(await readError(response, 'Login failed.'))

      const body = (await response.json()) as AuthResponse
      setUsername(body.username)
      setToken(body.token)
      setStatus(`Logged in as ${body.username}.`)
    } catch (err) {
      setError(toMessage(err))
      setStatus('Could not log in.')
    } finally {
      setIsLoggingIn(false)
    }
  }

  async function handleSignup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!signupName.trim()) {
      setError('Enter a name to sign up.')
      return
    }

    setIsSigningUp(true)
    setError('')
    setStatus('Signing up...')

    try {
      const response = await fetch('/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: signupName.trim() }),
      })

      if (!response.ok) throw new Error(await readError(response, 'Signup failed.'))

      setStatus(`Signed up as ${signupName.trim()}. You can now log in.`)
      setUsername(signupName.trim())
      setSignupName('')
    } catch (err) {
      setError(toMessage(err))
      setStatus('Could not sign up.')
    } finally {
      setIsSigningUp(false)
    }
  }

  async function handleSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!token) {
      setError('Log in before sending a message.')
      return
    }
    if (!draft.trim()) {
      setError('Write a message first.')
      return
    }

    const outgoing = draft.trim()
    setDraft('')
    setIsSending(true)
    setError('')
    setStatus('Thinking...')

    try {
      const response = await fetch('/chat/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ content: outgoing }),
      })

      if (!response.ok) throw new Error(await readError(response, 'Message failed.'))

      const body = (await response.json()) as ChatReplyResponse
      setMessages((current) => [...current, body.user_message, body.assistant_message])
      setStatus('Response stored in SQLite chat history.')
    } catch (err) {
      setDraft(outgoing)
      setError(toMessage(err))
      setStatus('The message did not go through.')
    } finally {
      setIsSending(false)
    }
  }

  async function handleDemoTrigger() {
    if (!token) {
      setError('Log in before starting the demo flow.')
      return
    }

    setIsTriggeringDemo(true)
    setError('')
    setStatus('Starting demo flow...')

    try {
      const response = await fetch('/chat/demo-trigger', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ course_name: DEMO_COURSE_NAME }),
      })

      if (!response.ok) throw new Error(await readError(response, 'Could not start the demo flow.'))

      const body = (await response.json()) as DemoTriggerResponse
      const notificationMessage = body.notification_message
      if (notificationMessage) {
        setMessages((current) => {
          if (current.some((message) => message.id === notificationMessage.id)) return current
          return [...current, notificationMessage]
        })
      }
      setStatus(`Demo flow started for ${DEMO_COURSE_NAME}.`)
    } catch (err) {
      setError(toMessage(err))
      setStatus('The demo flow did not start.')
    } finally {
      setIsTriggeringDemo(false)
    }
  }

  function handleLogout() {
    setToken('')
    setMessages([])
    setDraft('')
    setError('')
    setEvents([])
    setEventsLoaded(false)
    setActiveView('chat')
    setStatus('Logged out. Log in again to load chat history.')
  }

  return (
    <main className="app-shell">
      <section className="hero-panel">
        <p className="eyebrow">Hackton study chat</p>
        <h1>Ask the knowledge we already have.</h1>
        <p className="hero-copy">
          This tiny UI logs in through <code>/login</code>, loads chat history from SQLite,
          and sends each message to the backend for a grounded reply from our stored app data
          and cognee knowledge.
        </p>

        <div className="auth-row">
          <form className="login-card" onSubmit={handleLogin}>
            <label htmlFor="username">Log in</label>
            <div className="login-row">
              <input
                id="username"
                name="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="existing username"
                autoComplete="username"
              />
              <button type="submit" disabled={isLoggingIn}>
                {isLoggingIn ? 'Logging in...' : 'Log in'}
              </button>
            </div>
            <p className="helper-text">
              {token ? `Authenticated as ${username || 'user'}.` : 'Enter an existing username.'}
            </p>
          </form>

          <form className="login-card" onSubmit={handleSignup}>
            <label htmlFor="signup-name">Sign up</label>
            <div className="login-row">
              <input
                id="signup-name"
                name="signup-name"
                value={signupName}
                onChange={(event) => setSignupName(event.target.value)}
                placeholder="pick a name"
                autoComplete="off"
              />
              <button type="submit" disabled={isSigningUp}>
                {isSigningUp ? 'Signing up...' : 'Sign up'}
              </button>
            </div>
            <p className="helper-text">New here? Create an account.</p>
          </form>
        </div>

        <div className="status-card" aria-live="polite">
          <strong>Status</strong>
          <p>{status}</p>
          {error ? <p className="error-text">{error}</p> : null}
        </div>
      </section>

      <section className="chat-panel">
        <header className="chat-header">
          <div className="view-tabs">
            <button
              type="button"
              className={`tab-button ${activeView === 'chat' ? 'tab-active' : ''}`}
              onClick={() => setActiveView('chat')}
            >
              Chat
            </button>
            <button
              type="button"
              className={`tab-button ${activeView === 'events' ? 'tab-active' : ''}`}
              onClick={() => { setActiveView('events') }}
              disabled={!token}
            >
              Event Recommendations
            </button>
          </div>
          <div className="chat-header-actions">
            {activeView === 'chat' ? (
              <>
                <button
                  type="button"
                  className="subtle-button"
                  onClick={handleDemoTrigger}
                  disabled={!token || isTriggeringDemo}
                >
                  {isTriggeringDemo ? 'Starting demo...' : 'Run demo flow'}
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={handleLogout}
                  disabled={!token}
                >
                  Clear session
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="scan-button"
                  onClick={handleScanEvents}
                  disabled={!token || isScanning}
                >
                  {isScanning ? (
                    <><span className="scan-spinner" /> Scanning web…</>
                  ) : (
                    '🔍 Scan for events'
                  )}
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={handleLogout}
                  disabled={!token}
                >
                  Clear session
                </button>
              </>
            )}
          </div>
        </header>

        {activeView === 'chat' ? (
          <>
            <div className="message-list" ref={listRef}>
              {!token ? (
                <div className="empty-state">
                  <p>Log in to load your saved chat messages.</p>
                </div>
              ) : isLoadingHistory ? (
                <div className="empty-state">
                  <p>Loading chat history...</p>
                </div>
              ) : messages.length === 0 ? (
                <div className="empty-state">
                  <p>No saved messages yet.</p>
                </div>
              ) : (
                <>
                  {messages.map((message) => (
                    <article
                      key={message.id}
                      className={`message-bubble message-${message.author}`}
                    >
                      <div className="message-meta">
                        <span>{message.author === 'user' ? username || 'user' : 'system'}</span>
                        <span>
                          #{message.sequence_number}
                          {message.processing_ms != null ? ` · ${message.processing_ms} ms` : ''}
                        </span>
                      </div>
                      <p>{message.content}</p>
                    </article>
                  ))}
                  {isSending && (
                    <article className="message-bubble message-system typing-indicator">
                      <span /><span /><span />
                    </article>
                  )}
                </>
              )}
            </div>

            <form className="composer" onSubmit={handleSend}>
              <label htmlFor="message">Message</label>
              <textarea
                id="message"
                name="message"
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
                    : 'Login first to enable sending.'}
                </p>
                <button type="submit" disabled={!token || isSending}>
                  {isSending ? 'Sending...' : 'Send'}
                </button>
              </div>
            </form>
          </>
        ) : (
          <div className="events-panel">
            {isScanning ? (
              <div className="events-loading">
                <div className="events-spinner" />
                <p>Searching the web for events tailored to your interests…</p>
                <p className="helper-text">This may take up to 30 seconds.</p>
              </div>
            ) : isLoadingEvents ? (
              <div className="events-loading">
                <div className="events-spinner" />
                <p>Loading your event recommendations…</p>
              </div>
            ) : !token ? (
              <div className="empty-state">
                <p>Log in to discover personalised events.</p>
              </div>
            ) : events.length === 0 ? (
              <div className="empty-state">
                <div className="events-empty-icon">🎯</div>
                <p>No events found yet.</p>
                <p className="helper-text">
                  Hit <strong>Scan for events</strong> to let the AI search the web for career,
                  networking, and fun events based on your profile.
                </p>
                {scanCount !== null && (
                  <p className="helper-text">Last scan found {scanCount} events — none matched your profile closely enough.</p>
                )}
              </div>
            ) : (
              <>
                {scanCount !== null && (
                  <p className="events-scan-summary">
                    Last scan found <strong>{scanCount}</strong> events · showing top {events.length} by relevance score
                  </p>
                )}
                <div className="events-grid">
                  {events.map((event) => (
                    <EventCard key={event.id} event={event} />
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </section>
    </main>
  )
}

async function readError(response: Response, fallback: string) {
  try {
    const body = (await response.json()) as { detail?: string }
    return body.detail || fallback
  } catch {
    return fallback
  }
}

function toMessage(error: unknown) {
  if (error instanceof Error) return error.message
  return 'Unexpected error.'
}

export default App
