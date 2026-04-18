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

const TOKEN_KEY = 'hackton-chat-token'
const USERNAME_KEY = 'hackton-chat-username'
const DEMO_COURSE_NAME = 'Machine Learning'

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

    socket.addEventListener('open', () => {
      setStatus((current) =>
        current === 'Thinking...' ? current : 'Live chat connection ready.',
      )
    })

    socket.addEventListener('message', (event) => {
      const payload = JSON.parse(event.data) as ChatSocketPayload
      if (payload.type !== 'chat_message' || !payload.message) {
        return
      }
      const incomingMessage = payload.message

      setMessages((current) => {
        if (current.some((message) => message.id === incomingMessage.id)) {
          return current
        }
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

    return () => {
      socket.close()
    }
  }, [token])

  async function loadHistory(activeToken: string) {
    setIsLoadingHistory(true)
    setError('')

    try {
      const response = await fetch('/chat/history', {
        headers: {
          Authorization: `Bearer ${activeToken}`,
        },
      })

      if (!response.ok) {
        throw new Error(await readError(response, 'Could not load chat history.'))
      }

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
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username: username.trim() }),
      })

      if (!response.ok) {
        throw new Error(await readError(response, 'Login failed.'))
      }

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

      if (!response.ok) {
        throw new Error(await readError(response, 'Signup failed.'))
      }

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

      if (!response.ok) {
        throw new Error(await readError(response, 'Message failed.'))
      }

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

      if (!response.ok) {
        throw new Error(await readError(response, 'Could not start the demo flow.'))
      }

      const body = (await response.json()) as DemoTriggerResponse
      const notificationMessage = body.notification_message
      if (notificationMessage) {
        setMessages((current) => {
          if (current.some((message) => message.id === notificationMessage.id)) {
            return current
          }
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
          <div>
            <p className="eyebrow">Chat history</p>
            <h2>SQLite-backed conversation</h2>
          </div>
          <div className="chat-header-actions">
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
          </div>
        </header>

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
  if (error instanceof Error) {
    return error.message
  }

  return 'Unexpected error.'
}

export default App
