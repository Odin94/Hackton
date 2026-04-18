import { useEffect, useRef, useState, useCallback } from 'react'
import type { FormEvent } from 'react'
import './App.css'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type AuthResponse = { token: string; user_id: number; username: string }

type ChatMessage = {
  id: number
  user_id: number
  timestamp: string
  author: 'user' | 'system'
  sequence_number: number
  content: string
  processing_ms?: number | null
}

type ChatReplyResponse = { user_message: ChatMessage; assistant_message: ChatMessage }
type DemoTriggerResponse = { notification_message?: ChatMessage | null }
type ChatSocketPayload = { type: 'chat_message' | 'ack'; message?: ChatMessage; echo?: string }

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
type DiscoverResponse = { total_events_found: number; top_events: DiscoveredEvent[] }

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TOKEN_KEY = 'hackton-chat-token'
const USERNAME_KEY = 'hackton-chat-username'
const DEMO_COURSE_NAME = 'Machine Learning'

const CATEGORY_COLORS: Record<string, string> = {
  career: '#1a6b4a',
  networking: '#284d4b',
  fun: '#b85c00',
  other: '#5a4f45',
}

// ---------------------------------------------------------------------------
// Small components
// ---------------------------------------------------------------------------

function CategoryBadge({ category }: { category: string }) {
  return (
    <span className="category-badge" style={{ background: CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other }}>
      {category}
    </span>
  )
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, score))
  const hue = Math.round((pct / 100) * 120)
  return (
    <div className="score-bar-track" title={`Score: ${pct}/100`}>
      <div className="score-bar-fill" style={{ width: `${pct}%`, background: `hsl(${hue}, 60%, 38%)` }} />
      <span className="score-bar-label">{pct}/100</span>
    </div>
  )
}

function EventCard({ event }: { event: DiscoveredEvent }) {
  return (
    <article className="event-card">
      <div className="event-card-header">
        <CategoryBadge category={event.category} />
      </div>
      <h3 className="event-card-title">{event.title}</h3>
      <ScoreBar score={event.score} />
      {event.score_reasoning && <p className="event-reasoning">{event.score_reasoning}</p>}
      <div className="event-meta">
        {event.event_date && <span className="event-meta-item">📅 {event.event_date}</span>}
        {event.location && <span className="event-meta-item">📍 {event.location}</span>}
        {event.signup_deadline && <span className="event-meta-item">⏰ Signup by {event.signup_deadline}</span>}
      </div>
      <p className="event-description">
        {event.description.slice(0, 220)}{event.description.length > 220 ? '…' : ''}
      </p>
      {event.url && (
        <a className="event-link" href={event.url} target="_blank" rel="noopener noreferrer">
          View event →
        </a>
      )}
    </article>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function readError(response: Response, fallback: string) {
  try {
    const body = (await response.json()) as { detail?: string }
    return body.detail || fallback
  } catch {
    return fallback
  }
}

function toMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Unexpected error.'
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

function App() {
  const [username, setUsername] = useState(() => localStorage.getItem(USERNAME_KEY) ?? '')
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? '')

  // Auth page state
  const [authMode, setAuthMode] = useState<'login' | 'signup'>('login')
  const [authUsername, setAuthUsername] = useState(() => localStorage.getItem(USERNAME_KEY) ?? '')
  const [authError, setAuthError] = useState('')
  const [isSubmittingAuth, setIsSubmittingAuth] = useState(false)

  // Chat state
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [isLoadingHistory, setIsLoadingHistory] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [chatError, setChatError] = useState('')

  // WS connection indicator
  const [wsConnected, setWsConnected] = useState(false)

  // Demo
  const [isTriggeringDemo, setIsTriggeringDemo] = useState(false)

  // Events view
  const [activeView, setActiveView] = useState<'chat' | 'events'>('chat')
  const [events, setEvents] = useState<DiscoveredEvent[]>([])
  const [isScanning, setIsScanning] = useState(false)
  const [isLoadingEvents, setIsLoadingEvents] = useState(false)
  const [eventsLoaded, setEventsLoaded] = useState(false)
  const [scanCount, setScanCount] = useState<number | null>(null)
  const [eventsError, setEventsError] = useState('')

  const listRef = useRef<HTMLDivElement | null>(null)

  // Voice mode
  const [voiceMode, setVoiceMode] = useState(false)
  const [voiceState, setVoiceState] = useState<'idle' | 'recording' | 'transcribing' | 'thinking' | 'speaking'>('idle')
  const [voiceTranscript, setVoiceTranscript] = useState('')
  const [voiceError, setVoiceError] = useState('')
  const [elevenLabsKey, setElevenLabsKey] = useState('')
  const [elevenLabsVoiceId, setElevenLabsVoiceId] = useState('')
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<BlobPart[]>([])
  const currentAudioRef = useRef<HTMLAudioElement | null>(null)

  // Persist token + username
  useEffect(() => {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token)
      void loadHistory(token)
    } else {
      localStorage.removeItem(TOKEN_KEY)
      setMessages([])
    }
  }, [token])

  useEffect(() => {
    localStorage.setItem(USERNAME_KEY, username)
  }, [username])

  // Auto-scroll chat
  useEffect(() => {
    const list = listRef.current
    if (list) list.scrollTop = list.scrollHeight
  }, [messages])

  // WebSocket
  useEffect(() => {
    if (!token) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws?token=${encodeURIComponent(token)}`)

    socket.addEventListener('open', () => setWsConnected(true))

    socket.addEventListener('message', (event) => {
      const payload = JSON.parse(event.data) as ChatSocketPayload
      if (payload.type !== 'chat_message' || !payload.message) return
      const msg = payload.message
      setMessages((prev) => prev.some((m) => m.id === msg.id) ? prev : [...prev, msg])
    })

    socket.addEventListener('close', () => setWsConnected(false))
    socket.addEventListener('error', () => setWsConnected(false))

    return () => { socket.close(); setWsConnected(false) }
  }, [token])

  // Fetch ElevenLabs config when logged in
  useEffect(() => {
    if (!token) return
    fetch('/config', { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => r.json())
      .then((body: { elevenlabs_api_key: string; elevenlabs_voice_id: string }) => {
        setElevenLabsKey(body.elevenlabs_api_key)
        setElevenLabsVoiceId(body.elevenlabs_voice_id)
      })
      .catch(() => {})
  }, [token])

  // Load events when switching to events tab
  useEffect(() => {
    if (activeView === 'events' && token && !eventsLoaded) {
      void loadEvents()
    }
  }, [activeView, token])

  // ---------------------------------------------------------------------------
  // Voice handlers
  // ---------------------------------------------------------------------------

  function exitVoiceMode() {
    mediaRecorderRef.current?.stop()
    currentAudioRef.current?.pause()
    currentAudioRef.current = null
    setVoiceMode(false)
    setVoiceState('idle')
    setVoiceTranscript('')
    setVoiceError('')
  }

  const handleMicClick = useCallback(async () => {
    if (voiceState === 'recording') {
      mediaRecorderRef.current?.stop()
      return
    }
    if (voiceState !== 'idle') return

    setVoiceError('')
    setVoiceTranscript('')

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setVoiceError('Microphone access denied.')
      return
    }

    const recorder = new MediaRecorder(stream)
    audioChunksRef.current = []
    recorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data) }

    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop())
      setVoiceState('transcribing')

      try {
        // --- STT ---
        const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        const form = new FormData()
        form.append('file', blob, 'recording.webm')
        form.append('model_id', 'scribe_v1')
        const sttRes = await fetch('https://api.elevenlabs.io/v1/speech-to-text', {
          method: 'POST',
          headers: { 'xi-api-key': elevenLabsKey },
          body: form,
        })
        if (!sttRes.ok) throw new Error('Speech-to-text failed.')
        const { text } = (await sttRes.json()) as { text: string }
        if (!text.trim()) { setVoiceState('idle'); return }
        setVoiceTranscript(text)

        // --- Chat ---
        setVoiceState('thinking')
        const tempId = -Date.now()
        setMessages((prev) => [
          ...prev,
          { id: tempId, user_id: 0, timestamp: new Date().toISOString(), author: 'user', sequence_number: -1, content: text },
        ])
        const chatRes = await fetch('/chat/messages', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ content: text }),
        })
        if (!chatRes.ok) {
          setMessages((prev) => prev.filter((m) => m.id !== tempId))
          throw new Error('Chat request failed.')
        }
        const body = (await chatRes.json()) as ChatReplyResponse
        setMessages((prev) => {
          const without = prev.filter((m) => m.id !== tempId)
          return [
            ...without,
            ...(without.some((m) => m.id === body.user_message.id) ? [] : [body.user_message]),
            ...(without.some((m) => m.id === body.assistant_message.id) ? [] : [body.assistant_message]),
          ]
        })

        // --- TTS ---
        setVoiceState('speaking')
        const ttsRes = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${elevenLabsVoiceId}`, {
          method: 'POST',
          headers: { 'xi-api-key': elevenLabsKey, 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: body.assistant_message.content,
            model_id: 'eleven_multilingual_v2',
            voice_settings: { stability: 0.5, similarity_boost: 0.75 },
          }),
        })
        if (!ttsRes.ok) throw new Error('Text-to-speech failed.')
        const audioBlob = await ttsRes.blob()
        const audioUrl = URL.createObjectURL(audioBlob)
        const audio = new Audio(audioUrl)
        currentAudioRef.current = audio
        audio.onended = () => { URL.revokeObjectURL(audioUrl); setVoiceState('idle'); setVoiceTranscript('') }
        audio.onerror = () => { URL.revokeObjectURL(audioUrl); setVoiceState('idle') }
        await audio.play()
      } catch (err) {
        setVoiceError(toMessage(err))
        setVoiceState('idle')
      }
    }

    mediaRecorderRef.current = recorder
    recorder.start()
    setVoiceState('recording')
  }, [voiceState, elevenLabsKey, elevenLabsVoiceId, token])

  // ---------------------------------------------------------------------------
  // Auth handlers
  // ---------------------------------------------------------------------------

  async function handleLogin(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!authUsername.trim()) { setAuthError('Enter a username.'); return }

    setIsSubmittingAuth(true)
    setAuthError('')
    try {
      const res = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: authUsername.trim() }),
      })
      if (!res.ok) throw new Error(await readError(res, 'Login failed.'))
      const body = (await res.json()) as AuthResponse
      setUsername(body.username)
      setToken(body.token)
    } catch (err) {
      setAuthError(toMessage(err))
    } finally {
      setIsSubmittingAuth(false)
    }
  }

  async function handleSignup(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!authUsername.trim()) { setAuthError('Enter a username.'); return }

    setIsSubmittingAuth(true)
    setAuthError('')
    try {
      const res = await fetch('/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: authUsername.trim() }),
      })
      if (!res.ok) throw new Error(await readError(res, 'Signup failed.'))

      // Auto-login after signup
      const loginRes = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: authUsername.trim() }),
      })
      if (!loginRes.ok) throw new Error(await readError(loginRes, 'Login after signup failed.'))
      const body = (await loginRes.json()) as AuthResponse
      setUsername(body.username)
      setToken(body.token)
    } catch (err) {
      setAuthError(toMessage(err))
    } finally {
      setIsSubmittingAuth(false)
    }
  }

  function handleLogout() {
    setToken('')
    setUsername('')
    setAuthUsername('')
    setMessages([])
    setDraft('')
    setChatError('')
    setEvents([])
    setEventsLoaded(false)
    setActiveView('chat')
    setWsConnected(false)
  }

  // ---------------------------------------------------------------------------
  // Chat handlers
  // ---------------------------------------------------------------------------

  async function loadHistory(activeToken: string) {
    setIsLoadingHistory(true)
    setChatError('')
    try {
      const res = await fetch('/chat/history', { headers: { Authorization: `Bearer ${activeToken}` } })
      if (!res.ok) throw new Error(await readError(res, 'Could not load chat history.'))
      const body = (await res.json()) as { messages: ChatMessage[] }
      setMessages(body.messages)
    } catch (err) {
      setMessages([])
      setToken('')
      setChatError(toMessage(err))
    } finally {
      setIsLoadingHistory(false)
    }
  }

  async function handleSend(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!draft.trim()) return

    const outgoing = draft.trim()
    setDraft('')
    setIsSending(true)
    setChatError('')

    // Optimistic: show message immediately with a temporary negative ID
    const tempId = -Date.now()
    setMessages((prev) => [
      ...prev,
      { id: tempId, user_id: 0, timestamp: new Date().toISOString(), author: 'user', sequence_number: -1, content: outgoing },
    ])

    try {
      const res = await fetch('/chat/messages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ content: outgoing }),
      })
      if (!res.ok) throw new Error(await readError(res, 'Message failed.'))
      const body = (await res.json()) as ChatReplyResponse

      setMessages((prev) => {
        const without = prev.filter((m) => m.id !== tempId)
        const hasUser = without.some((m) => m.id === body.user_message.id)
        const hasAssistant = without.some((m) => m.id === body.assistant_message.id)
        return [
          ...without,
          ...(hasUser ? [] : [body.user_message]),
          ...(hasAssistant ? [] : [body.assistant_message]),
        ]
      })
    } catch (err) {
      // Remove the optimistic message and restore the draft
      setMessages((prev) => prev.filter((m) => m.id !== tempId))
      setDraft(outgoing)
      setChatError(toMessage(err))
    } finally {
      setIsSending(false)
    }
  }

  async function handleDemoTrigger() {
    setIsTriggeringDemo(true)
    setChatError('')
    try {
      const res = await fetch('/chat/demo-trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ course_name: DEMO_COURSE_NAME }),
      })
      if (!res.ok) throw new Error(await readError(res, 'Could not start the demo flow.'))
      const body = (await res.json()) as DemoTriggerResponse
      if (body.notification_message) {
        setMessages((prev) =>
          prev.some((m) => m.id === body.notification_message!.id) ? prev : [...prev, body.notification_message!]
        )
      }
    } catch (err) {
      setChatError(toMessage(err))
    } finally {
      setIsTriggeringDemo(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Events handlers
  // ---------------------------------------------------------------------------

  async function loadEvents() {
    setIsLoadingEvents(true)
    setEventsError('')
    try {
      const res = await fetch('/events/', { headers: { Authorization: `Bearer ${token}` } })
      if (!res.ok) throw new Error(await readError(res, 'Could not load events.'))
      const body = (await res.json()) as EventListResponse
      setEvents(body.events)
      setEventsLoaded(true)
    } catch (err) {
      setEventsError(toMessage(err))
    } finally {
      setIsLoadingEvents(false)
    }
  }

  async function handleScanEvents() {
    setIsScanning(true)
    setEventsError('')
    setScanCount(null)
    try {
      const res = await fetch('/events/discover', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(await readError(res, 'Event scan failed.'))
      const body = (await res.json()) as DiscoverResponse
      setScanCount(body.total_events_found)
      setEventsLoaded(false)
      await loadEvents()
    } catch (err) {
      setEventsError(toMessage(err))
    } finally {
      setIsScanning(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Render — Auth page
  // ---------------------------------------------------------------------------

  if (!token) {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <div className="auth-brand">
            <div className="brand-dot" />
            <span>TumTum</span>
          </div>
          <p className="auth-tagline">Your AI-powered study companion</p>

          <div className="auth-tabs">
            <button
              type="button"
              className={`tab-button ${authMode === 'login' ? 'tab-active' : ''}`}
              onClick={() => { setAuthMode('login'); setAuthError('') }}
            >
              Log in
            </button>
            <button
              type="button"
              className={`tab-button ${authMode === 'signup' ? 'tab-active' : ''}`}
              onClick={() => { setAuthMode('signup'); setAuthError('') }}
            >
              Sign up
            </button>
          </div>

          <form onSubmit={authMode === 'login' ? handleLogin : handleSignup} className="auth-form">
            <input
              value={authUsername}
              onChange={(e) => setAuthUsername(e.target.value)}
              placeholder={authMode === 'login' ? 'Your username' : 'Pick a username'}
              autoComplete={authMode === 'login' ? 'username' : 'off'}
              autoFocus
            />
            <button type="submit" className="auth-submit" disabled={isSubmittingAuth}>
              {isSubmittingAuth
                ? authMode === 'login' ? 'Logging in…' : 'Creating account…'
                : authMode === 'login' ? 'Log in' : 'Create account'}
            </button>
          </form>

          {authError && <p className="auth-error">{authError}</p>}
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Render — App (logged in)
  // ---------------------------------------------------------------------------

  return (
    <div className="app-shell">
      <nav className="app-nav">
        <div className="nav-brand">
          <div className="brand-dot" />
          <span>TumTum</span>
        </div>

        <div className="nav-tabs">
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
            onClick={() => setActiveView('events')}
          >
            Events
          </button>
        </div>

        <div className="nav-right">
          {activeView === 'chat' && (
            <button
              type="button"
              className="nav-action-button"
              onClick={handleDemoTrigger}
              disabled={isTriggeringDemo}
            >
              {isTriggeringDemo ? 'Starting…' : 'Run demo'}
            </button>
          )}
          {activeView === 'events' && (
            <button
              type="button"
              className="scan-button"
              onClick={handleScanEvents}
              disabled={isScanning}
            >
              {isScanning ? <><span className="scan-spinner" /> Scanning…</> : '🔍 Scan'}
            </button>
          )}
          <div className="nav-user">
            <span className={`ws-dot ${wsConnected ? 'ws-live' : ''}`} title={wsConnected ? 'Connected' : 'Disconnected'} />
            <span className="nav-username">{username}</span>
          </div>
          <button type="button" className="nav-logout" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </nav>

      <main className="app-content">
        {activeView === 'chat' ? (
          <>
            <div className="message-list" ref={listRef}>
              {isLoadingHistory ? (
                <div className="empty-state"><p>Loading chat history…</p></div>
              ) : messages.length === 0 ? (
                <div className="empty-state"><p>No messages yet. Say hello!</p></div>
              ) : (
                <div className="chat-col">
                  {messages.map((msg) => (
                    <article
                      key={msg.id}
                      className={`message-bubble message-${msg.author}${msg.id < 0 ? ' message-pending' : ''}`}
                    >
                      <div className="message-meta">
                        <span>{msg.author === 'user' ? username : 'TumTum'}</span>
                        {msg.sequence_number >= 0 && (
                          <span>
                            #{msg.sequence_number}
                            {msg.processing_ms != null ? ` · ${msg.processing_ms} ms` : ''}
                          </span>
                        )}
                      </div>
                      <p>{msg.content}</p>
                    </article>
                  ))}
                  {isSending && (
                    <article className="message-bubble message-system typing-indicator">
                      <span /><span /><span />
                    </article>
                  )}
                </div>
              )}
            </div>

            {voiceMode ? (
              <div className="voice-composer">
                <div className="voice-transcript-row">
                  {voiceTranscript && <p className="voice-transcript">"{voiceTranscript}"</p>}
                </div>
                <button
                  type="button"
                  className={`mic-button ${voiceState === 'recording' ? 'mic-recording' : ''}`}
                  onClick={handleMicClick}
                  disabled={voiceState !== 'idle' && voiceState !== 'recording'}
                  aria-label={voiceState === 'recording' ? 'Stop recording' : 'Start recording'}
                >
                  {voiceState === 'recording' ? '⏹' : '🎙'}
                </button>
                <p className="voice-status-text">
                  {voiceState === 'idle' && 'Tap to speak'}
                  {voiceState === 'recording' && 'Listening…'}
                  {voiceState === 'transcribing' && 'Transcribing…'}
                  {voiceState === 'thinking' && 'Thinking…'}
                  {voiceState === 'speaking' && 'Speaking…'}
                </p>
                {voiceError && <p className="inline-error">{voiceError}</p>}
                <button type="button" className="voice-exit-btn" onClick={exitVoiceMode}>
                  Switch to text
                </button>
              </div>
            ) : (
              <form className="composer" onSubmit={handleSend}>
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); e.currentTarget.form?.requestSubmit() } }}
                  placeholder="Ask about your study notes, quizzes, or schedule…"
                  rows={3}
                  disabled={isSending}
                />
                <div className="composer-row">
                  {chatError
                    ? <p className="inline-error">{chatError}</p>
                    : <p className="composer-hint">⌘ Enter to send</p>}
                  <div className="composer-actions">
                    <button
                      type="button"
                      className="voice-toggle-btn"
                      onClick={() => setVoiceMode(true)}
                      title="Switch to voice mode"
                    >
                      🎙
                    </button>
                    <button type="submit" disabled={isSending || !draft.trim()}>
                      {isSending ? 'Sending…' : 'Send'}
                    </button>
                  </div>
                </div>
              </form>
            )}
          </>
        ) : (
          <div className="events-panel">
            {isScanning ? (
              <div className="events-loading">
                <div className="events-spinner" />
                <p>Asking the AI to find events for you…</p>
                <p className="helper-text">This may take up to 30 seconds.</p>
              </div>
            ) : isLoadingEvents ? (
              <div className="events-loading">
                <div className="events-spinner" />
                <p>Loading recommendations…</p>
              </div>
            ) : eventsError ? (
              <div className="empty-state"><p className="inline-error">{eventsError}</p></div>
            ) : events.length === 0 ? (
              <div className="empty-state">
                <div className="events-empty-icon">🎯</div>
                <p>No recommendations yet.</p>
                <p className="helper-text">
                  Hit <strong>Scan</strong> to let the AI find career, networking, and fun events
                  matched to your profile.
                </p>
              </div>
            ) : (
              <>
                <h2 className="events-title">Tailored event recommendations for April/May</h2>
                <div className="events-grid">
                  {events.map((event) => <EventCard key={event.id} event={event} />)}
                </div>
              </>
            )}
          </div>
        )}
      </main>
    </div>
  )
}

export default App
