"use client"

import { useState, useEffect, useCallback } from "react"
import type { EmailDraft, EmailMessage, Practice } from "@/lib/types"
import {
  getEmailDraft,
  regenerateEmailDraft,
  saveEmailDraft,
  sendEmail,
  getEmailMessages,
  pollEmailReplies,
  markEmailReplied,
} from "@/lib/api"
import EmailRecipient from "./email-recipient"
import EmailComposer from "./email-composer"
import EmailThread from "./email-thread"

interface EmailPanelProps {
  practice: Practice
  onPracticeUpdate: (next: Partial<Practice>) => void
}

export default function EmailPanel({ practice, onPracticeUpdate }: EmailPanelProps) {
  const [draft, setDraft] = useState<EmailDraft>({ subject: "", body: "" })
  const [messages, setMessages] = useState<EmailMessage[]>([])
  const [isRegenerating, setIsRegenerating] = useState(false)
  const [isPolling, setIsPolling] = useState(false)

  const loadDraft = useCallback(async () => {
    const d = await getEmailDraft(practice.place_id)
    setDraft(d)
  }, [practice.place_id])

  const loadMessages = useCallback(async () => {
    const m = await getEmailMessages(practice.place_id)
    setMessages(m)
  }, [practice.place_id])

  useEffect(() => {
    if (!practice.email) return
    loadDraft()
    loadMessages()
  }, [practice.email, loadDraft, loadMessages])

  async function handleSave(next: EmailDraft) {
    const saved = await saveEmailDraft(practice.place_id, next)
    setDraft(saved)
  }

  async function handleRegenerate() {
    setIsRegenerating(true)
    try {
      const fresh = await regenerateEmailDraft(practice.place_id)
      setDraft(fresh)
    } finally {
      setIsRegenerating(false)
    }
  }

  async function handleSend() {
    await sendEmail(practice.place_id)
    await loadMessages()
    onPracticeUpdate({ status: "CONTACTED" })
    setDraft({ subject: "", body: "" })
  }

  async function handlePoll() {
    setIsPolling(true)
    try {
      const result = await pollEmailReplies(practice.place_id)
      if (result.new_messages.length > 0) {
        onPracticeUpdate({ status: "FOLLOW UP" })
      }
      await loadMessages()
    } finally {
      setIsPolling(false)
    }
  }

  async function handleMarkReplied() {
    await markEmailReplied(practice.place_id)
    onPracticeUpdate({ status: "FOLLOW UP" })
    await loadMessages()
  }

  return (
    <div className="space-y-4">
      <EmailRecipient
        placeId={practice.place_id}
        email={practice.email}
        onChange={(email) => onPracticeUpdate({ email })}
      />

      {practice.email ? (
        <>
          <EmailComposer
            draft={draft}
            canSend={Boolean(practice.email) && Boolean(draft.subject) && Boolean(draft.body)}
            recipient={practice.email}
            onSave={handleSave}
            onRegenerate={handleRegenerate}
            onSend={handleSend}
            isRegenerating={isRegenerating}
          />
          <EmailThread
            messages={messages}
            onPoll={handlePoll}
            onMarkReplied={handleMarkReplied}
            isPolling={isPolling}
          />
        </>
      ) : (
        <p className="text-xs text-gray-500 dark:text-gray-400">Add an email address to compose and send.</p>
      )}
    </div>
  )
}
