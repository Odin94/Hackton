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

const SCENE_B_USER_LINE =
  "hmm yeah, the powerset thing got away from me. got time for a reset?"
const SCENE_G_USER_LINE =
  "yeah actually feeling way better. i'll hit the library this afternoon."

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

async function typeInto(
  setDraft: (text: string) => void,
  text: string,
  perChar = 35,
) {
  setDraft('')
  for (let i = 0; i < text.length; i += 1) {
    setDraft(text.slice(0, i + 1))
    await sleep(perChar)
  }
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
  const listRef = useRef<HTMLDivElement | null>(null)
  const quizDoneResolverRef = useRef<(() => void) | null>(null)

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
    await postJSON('/demo/scripted-turn', {
      user_content: userContent,
      system_content: systemContent,
    })
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
      setStatus('Scene A — TumTum proactive ping')
      await sendSystemMessage(SCENE_A_PING)
      await sleep(3500)

      setStatus('Scene B — Odin replies')
      await typeInto(setDraft, SCENE_B_USER_LINE)
      await sleep(500)

      setStatus('Scene C — scripted coach reply')
      const outgoing = SCENE_B_USER_LINE
      setDraft('')
      await sendScriptedTurn(outgoing, SCENE_C_REPLY)
      await sleep(4000)

      setStatus('Scene D — 3-question MC quiz')
      setQuizAutoPlay(true)
      setQuizOpen(true)
      const quizDone = new Promise<void>((resolve) => {
        quizDoneResolverRef.current = resolve
      })
      await quizDone

      setStatus('Scene E — performance recap (auto-fires from quiz)')
      await sleep(4500)

      setStatus('Scene F — mood check-in')
      await sendSystemMessage(SCENE_F_MOOD_PING)
      await sleep(3500)

      setStatus('Scene G — live LLM adaptive reply')
      await typeInto(setDraft, SCENE_G_USER_LINE)
      await sleep(500)
      const live = SCENE_G_USER_LINE
      setDraft('')
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
        setMessages((current) => {
          const next = [...current]
          for (const message of [body.user_message, body.assistant_message]) {
            if (!next.some((m) => m.id === message.id)) {
              next.push(message)
            }
          }
          return next
        })
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

  async function handleQuizComplete(answers: number[]) {
    setQuizOpen(false)
    setQuizAutoPlay(false)
    const correctCount = answers.reduce(
      (acc, choice, idx) => acc + (choice === FALLBACK_QUIZ.questions[idx].correct_index ? 1 : 0),
      0,
    )
    try {
      await saveQuizResults(correctCount, answers.length)
      setStatus(`Quiz saved: ${correctCount}/${answers.length}`)
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
  }

  return (
    <main className="app-shell">
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
    </main>
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

function toMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }
  return 'Unexpected error.'
}

export default App
