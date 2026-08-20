"use client"

import { Mail, ExternalLink, Phone, User } from "lucide-react"
import type { Contact } from "@/lib/types"

interface ContactCardProps {
  contact: Contact
}

function EmailRow({ email, label }: { email: string; label: string }) {
  return (
    <div className="flex items-center gap-1.5 mt-1.5">
      <a
        href={`mailto:${email}`}
        onClick={(e) => e.stopPropagation()}
        className="inline-flex items-center gap-1 text-[11px] text-gray-600 dark:text-[#d9d9d9] hover:text-teal-700 dark:hover:text-teal-400 min-w-0"
        title={email}
      >
        <Mail className="w-3 h-3 shrink-0" />
        <span className="truncate max-w-[160px]">{email}</span>
      </a>
      <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full bg-gray-200/70 dark:bg-white/10 text-gray-500 dark:text-gray-400">
        {label}
      </span>
    </div>
  )
}

export default function ContactCard({ contact }: ContactCardProps) {
  const fullName =
    [contact.first_name, contact.last_name].filter(Boolean).join(" ") || "Unknown"

  return (
    <div className="rounded-lg border border-gray-200/60 dark:border-white/10 bg-gray-50/60 dark:bg-white/5 p-3">
      <div className="flex items-center gap-1.5 text-xs">
        <User className="w-3 h-3 text-gray-500 dark:text-gray-400 shrink-0" />
        <span className="font-medium text-gray-800 dark:text-[#d9d9d9] truncate">{fullName}</span>
        {contact.title && (
          <span className="text-gray-400 dark:text-gray-500 truncate">· {contact.title}</span>
        )}
      </div>

      {contact.work_email && <EmailRow email={contact.work_email} label="Work" />}
      {contact.personal_email && <EmailRow email={contact.personal_email} label="Personal" />}

      {contact.phone && (
        <div className="flex items-center gap-1.5 mt-1.5">
          <a
            href={`tel:${contact.phone}`}
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 text-[11px] text-gray-600 dark:text-[#d9d9d9] hover:text-teal-700 dark:hover:text-teal-400"
            title={contact.phone}
          >
            <Phone className="w-3 h-3 shrink-0" />
            <span className="truncate max-w-[160px]">{contact.phone}</span>
          </a>
        </div>
      )}

      {contact.linkedin_url && (
        <div className="flex items-center gap-2 mt-1.5">
          <a
            href={contact.linkedin_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-0.5 text-[11px] text-blue-600 dark:text-blue-300 hover:text-blue-800"
            title="LinkedIn"
          >
            <ExternalLink className="w-3 h-3" />
            <span>LinkedIn</span>
          </a>
        </div>
      )}
    </div>
  )
}
