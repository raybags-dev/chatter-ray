import React, { useEffect, useRef, useState } from 'react'
import { useChat } from './useChat.js'

function Avatar({ sender }) {
  if (sender === 'user') {
    return (
      <span className="flex-none w-7 h-7 rounded-full bg-sky-500/20 text-sky-400 text-xs flex items-center justify-center font-bold">
        U
      </span>
    )
  }
  if (sender === 'human') {
    return (
      <span className="flex-none w-7 h-7 rounded-full bg-emerald-500/20 text-emerald-400 text-xs flex items-center justify-center">
        R
      </span>
    )
  }
  return (
    <span className="flex-none w-7 h-7 rounded-full bg-violet-500/20 text-violet-400 text-xs flex items-center justify-center">
      AI
    </span>
  )
}

function Message({ msg }) {
  const isUser = msg.sender === 'user'
  return (
    <div className={`flex gap-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <Avatar sender={msg.sender} />
      <div
        className={`max-w-[75%] px-3 py-2 rounded-2xl text-sm leading-relaxed ${
          isUser
            ? 'bg-sky-600 text-white rounded-tr-sm'
            : msg.sender === 'system'
            ? 'bg-slate-700/50 text-slate-400 italic text-xs'
            : 'bg-slate-800 text-slate-100 rounded-tl-sm border border-slate-700'
        }`}
      >
        {msg.content}
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex gap-2">
      <Avatar sender="agent" />
      <div className="bg-slate-800 border border-slate-700 rounded-2xl rounded-tl-sm px-3 py-2">
        <span className="flex gap-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce"
              style={{ animationDelay: `${i * 0.15}s` }}
            />
          ))}
        </span>
      </div>
    </div>
  )
}

export default function ChatWidget() {
  const { messages, connected, typing, send } = useChat()
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const bottomRef = useRef(null)

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, open, typing])

  function handleSend(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || !connected) return
    send(text)
    setInput('')
  }

  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col items-end gap-3">
      {/* Chat panel */}
      {open && (
        <div className="w-80 sm:w-96 h-[500px] bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl flex flex-col overflow-hidden">
          {/* Header */}
          <div className="flex items-center gap-3 px-4 py-3 bg-slate-800 border-b border-slate-700">
            <span className="w-2 h-2 rounded-full bg-emerald-400" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-white truncate">Ray's Assistant</p>
              <p className="text-xs text-slate-400">
                {connected ? 'Online' : 'Connecting…'}
              </p>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="text-slate-400 hover:text-white transition-colors"
              aria-label="Close chat"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3 scroll-smooth">
            {messages.map((m, i) => (
              <Message key={i} msg={m} />
            ))}
            {typing && <TypingIndicator />}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <form
            onSubmit={handleSend}
            className="flex gap-2 px-3 py-3 border-t border-slate-700 bg-slate-800/50"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={connected ? 'Ask me anything…' : 'Connecting…'}
              disabled={!connected}
              className="flex-1 bg-slate-700 text-white text-sm rounded-xl px-3 py-2 outline-none placeholder:text-slate-500 disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!connected || !input.trim()}
              className="p-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-40 text-white rounded-xl transition-colors"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </form>
        </div>
      )}

      {/* FAB */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-14 h-14 bg-sky-600 hover:bg-sky-500 text-white rounded-full shadow-lg flex items-center justify-center transition-all active:scale-95"
        aria-label="Open chat"
      >
        {open ? (
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        ) : (
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        )}
      </button>
    </div>
  )
}
