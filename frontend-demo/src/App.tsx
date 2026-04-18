import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'
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

const TOKEN_KEY = 'tumtum-demo-token'

type ArmedScript = null | 'scene-c'

function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? '')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState('Booting demo...')
  const [armedScript, setArmedScript] = useState<ArmedScript>(null)
  const [quizOpen, setQuizOpen] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const listRef = useRef<HTMLDivElement | null>(null)
  const armedRef = useRef<ArmedScript>(null)

  useEffect(() => {
    armedRef.current = armedScript
  }, [armedScript])

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
    setStatus(`Ready as ${DEMO_USERNAME} · Live`)
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
    }
  }, [token])

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!event.shiftKey) {
        return
      }
      const target = event.target as HTMLElement | null
      const tag = target?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') {
        return
      }
      const key = event.key.toLowerCase()
      if (key === 'p') {
        event.preventDefault()
        void triggerSystemMessage(SCENE_A_PING, 'Scene A ping queued')
      } else if (key === 'c') {
        event.preventDefault()
        setArmedScript('scene-c')
        setStatus('Scene C armed — next send will reply with scripted coach line.')
      } else if (key === 'l') {
        event.preventDefault()
        setArmedScript(null)
        setStatus('Live mode — next send goes through the LLM (Scene G).')
      } else if (key === 'q') {
        event.preventDefault()
        setQuizOpen(true)
      } else if (key === 'f') {
        event.preventDefault()
        void triggerSystemMessage(SCENE_F_MOOD_PING, 'Scene F mood ping queued')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [token])

  async function triggerSystemMessage(content: string, logLabel: string) {
    if (!token) {
      return
    }
    try {
      const response = await fetch('/demo/system-message', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ content }),
      })
      if (!response.ok) {
        throw new Error(`system-message ${response.status}`)
      }
      setStatus(logLabel)
    } catch (err) {
      setStatus(`${logLabel} failed: ${toMessage(err)}`)
    }
  }

  async function handleSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token || !draft.trim() || isSending) {
      return
    }
    const outgoing = draft.trim()
    const scripted = armedRef.current
    setDraft('')
    setIsSending(true)
    setStatus(scripted ? 'Scripted reply...' : 'Live LLM reply...')

    try {
      if (scripted === 'scene-c') {
        const response = await fetch('/demo/scripted-turn', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            user_content: outgoing,
            system_content: SCENE_C_REPLY,
          }),
        })
        if (!response.ok) {
          throw new Error(`scripted-turn ${response.status}`)
        }
        setArmedScript(null)
        setStatus('Scene C fired.')
      } else {
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
      }
    } catch (err) {
      setDraft(outgoing)
      setStatus(`Send failed: ${toMessage(err)}`)
    } finally {
      setIsSending(false)
    }
  }

  async function handleQuizComplete(answers: number[]) {
    setQuizOpen(false)
    const correctCount = answers.reduce(
      (acc, choice, idx) => acc + (choice === FALLBACK_QUIZ.questions[idx].correct_index ? 1 : 0),
      0,
    )
    try {
      await fetch('/demo/quiz-results', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          title: FALLBACK_QUIZ.title,
          topic: FALLBACK_QUIZ.topic,
          estimated_duration_minutes: 5,
          questions: FALLBACK_QUIZ.questions,
          correct_answers: correctCount,
          false_answers: answers.length - correctCount,
        }),
      })
      setStatus(`Quiz saved: ${correctCount}/${answers.length}`)
    } catch (err) {
      setStatus(`Quiz save failed: ${toMessage(err)}`)
    }

    setTimeout(() => {
      void triggerSystemMessage(
        `${SCENE_E_RECAP_PREFIX}${correctCount}/${answers.length} on the powerset drill.${SCENE_E_RECAP_BODY}`,
        'Scene E recap queued',
      )
    }, 600)
  }

  return (
    <main className="app-shell">
      <header className="demo-header">
        <div>
          <p className="eyebrow">TumTum · Study Coach</p>
          <h1>Chat with Odin</h1>
        </div>
        <div className="status-pill" aria-live="polite">
          <span className={armedScript ? 'pill pill-armed' : 'pill pill-live'}>
            {armedScript === 'scene-c' ? 'Scene C armed' : 'Live'}
          </span>
          <span className="status-text">{status}</span>
        </div>
      </header>

      <section className="chat-panel">
        <div className="message-list" ref={listRef}>
          {messages.length === 0 ? (
            <div className="empty-state">
              <p>No messages yet.</p>
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
                <p>{message.content}</p>
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
            placeholder={
              armedScript === 'scene-c'
                ? 'Type anything — scripted coach line will reply.'
                : 'Write a message...'
            }
            rows={3}
            disabled={!token || isSending}
          />
          <div className="composer-actions">
            <p className="helper-text">
              Shift+P ping · Shift+C arm · Shift+Q quiz · Shift+F mood · Shift+L live
            </p>
            <button type="submit" disabled={!token || isSending || !draft.trim()}>
              {isSending ? 'Sending...' : 'Send'}
            </button>
          </div>
        </form>
      </section>

      {quizOpen ? (
        <QuizOverlay
          questions={FALLBACK_QUIZ.questions}
          title={FALLBACK_QUIZ.title}
          onClose={() => setQuizOpen(false)}
          onComplete={handleQuizComplete}
        />
      ) : null}
    </main>
  )
}

type QuizOverlayProps = {
  questions: DemoQuizQuestion[]
  title: string
  onClose: () => void
  onComplete: (answers: number[]) => void
}

function QuizOverlay({ questions, title, onClose, onComplete }: QuizOverlayProps) {
  const [index, setIndex] = useState(0)
  const [answers, setAnswers] = useState<number[]>([])
  const [selected, setSelected] = useState<number | null>(null)

  const current = questions[index]
  const isLast = index === questions.length - 1
  const revealed = selected !== null

  function handleSelect(optionIdx: number) {
    if (revealed) {
      return
    }
    setSelected(optionIdx)
  }

  function handleNext() {
    if (selected === null) {
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
          <button type="button" className="ghost-button" onClick={onClose}>
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
                disabled={revealed}
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
          <button
            type="button"
            onClick={handleNext}
            disabled={selected === null}
          >
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

function toMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }
  return 'Unexpected error.'
}

export default App
