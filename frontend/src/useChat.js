import { useCallback, useEffect, useRef, useState } from 'react'

const WS_BASE = import.meta.env.VITE_WS_URL ?? ''

function sessionId() {
  let id = sessionStorage.getItem('rc_sid')
  if (!id) {
    id = crypto.randomUUID()
    sessionStorage.setItem('rc_sid', id)
  }
  return id
}

export function useChat() {
  const [messages, setMessages] = useState([])
  const [connected, setConnected] = useState(false)
  const [typing, setTyping] = useState(false)
  const wsRef = useRef(null)
  const sid = useRef(sessionId())

  useEffect(() => {
    const url = `${WS_BASE}/ws/${sid.current}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'msg') {
          setTyping(false)
          setMessages((prev) => [...prev, msg])
        }
      } catch {}
    }

    return () => ws.close()
  }, [])

  const send = useCallback((content) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    setMessages((prev) => [...prev, { sender: 'user', content, ts: Date.now() / 1000 }])
    setTyping(true)
    wsRef.current.send(JSON.stringify({ type: 'msg', content }))
  }, [])

  return { messages, connected, typing, send, sessionId: sid.current }
}
