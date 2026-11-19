import { useEffect, useState } from 'react'

import { Composer } from '@/components/Composer'
import { ProviderDialog } from '@/components/ProviderDialog'
import { ProviderView } from '@/components/ProviderView'
import { SessionSidebar } from '@/components/SessionSidebar'
import { TopBar, type PageTab } from '@/components/TopBar'
import { Transcript } from '@/components/Transcript'
import type { ApprovalDecision } from '@/components/ApprovalCard'
import { useSteward } from '@/hooks/useSteward'
import type { Block } from '@/lib/blocks'

export default function App() {
  const steward = useSteward()
  const [draft, setDraft] = useState('')
  const [showReasoning, setShowReasoning] = useState(false)
  const [tab, setTab] = useState<PageTab>('chat')
  const [settingsOpen, setSettingsOpen] = useState(false)

  useEffect(() => {
    const stored = localStorage.getItem('steward.reasoning')
    if (stored === null) return
    setShowReasoning(stored === '1')
  }, [])

  useEffect(() => {
    if (!steward.status) return
    const stored = localStorage.getItem('steward.reasoning')
    if (stored === null) setShowReasoning(steward.status.show_reasoning)
  }, [steward.status])

  const toggleReasoning = (value: boolean) => {
    setShowReasoning(value)
    localStorage.setItem('steward.reasoning', value ? '1' : '0')
  }

  const handleSend = async (text: string) => {
    const accepted = await steward.send(text)
    if (accepted) setDraft('')
  }

  const handleDecide = (block: Block, decision: ApprovalDecision) => {
    if (block.kind !== 'approval') return
    void steward.decide(block.approvalId, block.key, decision)
  }

  const pickSuggestion = (text: string) => {
    setDraft(text)
  }

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-surface text-foreground">
      <SessionSidebar
        status={steward.status}
        sessions={steward.sessions}
        session={steward.session}
        onNew={steward.newSession}
        onSelect={steward.selectSession}
      />

      <main className="flex min-w-0 flex-1 flex-col bg-background">
        <TopBar
          tab={tab}
          onTab={setTab}
          status={steward.status}
          session={steward.session}
          showReasoning={showReasoning}
          onToggleReasoning={toggleReasoning}
          onPermission={steward.setPermission}
          onOpenSettings={() => setSettingsOpen(true)}
        />

        {tab === 'provider' ? (
          <ProviderView
            provider={steward.provider}
            profiles={steward.profiles}
            note={steward.note}
            onEdit={() => setSettingsOpen(true)}
            onApply={steward.applyProfile}
            onDelete={steward.deleteProfile}
          />
        ) : (
          <>
            <Transcript
              blocks={steward.blocks}
              showReasoning={showReasoning}
              busy={steward.busy}
              onDecide={handleDecide}
              onPick={pickSuggestion}
            />

            <Composer
              value={draft}
              busy={steward.busy}
              connected={steward.connected}
              note={steward.note}
              onChange={setDraft}
              onSend={handleSend}
            />
          </>
        )}
      </main>

      <ProviderDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        provider={steward.provider}
        onSave={steward.saveProvider}
        onTest={steward.testProvider}
        onSyncModels={steward.syncModels}
        onSaveProfile={steward.saveProfile}
        onNote={steward.setNote}
      />
    </div>
  )
}
