import { useEffect, useState } from 'react'

import { Composer } from '@/components/Composer'
import { LoginView } from '@/components/LoginView'
import { LogsView } from '@/components/LogsView'
import { SessionSidebar } from '@/components/SessionSidebar'
import { SettingsDialog } from '@/components/SettingsDialog'
import { TopBar } from '@/components/TopBar'
import { Transcript } from '@/components/Transcript'
import type { ApprovalDecision } from '@/components/ApprovalCard'
import { useSteward } from '@/hooks/useSteward'
import type { Block } from '@/lib/blocks'

export default function App() {
  const steward = useSteward()
  const [draft, setDraft] = useState('')
  const [showReasoning, setShowReasoning] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [activeView, setActiveView] = useState<'chat' | 'logs'>('chat')

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

  const handleNewSession = () => {
    setActiveView('chat')
    void steward.newSession()
  }

  const handleSelectSession = (id: string) => {
    setActiveView('chat')
    void steward.selectSession(id)
  }

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-surface text-foreground">
      {steward.user === null ? (
        <LoginView onLogin={steward.login} />
      ) : (
        <>
          <SessionSidebar
            sessions={steward.sessions}
            session={steward.session}
            activeView={activeView}
            onNew={handleNewSession}
            onSelect={handleSelectSession}
            onToggleLogs={() => setActiveView((current) => (current === 'logs' ? 'chat' : 'logs'))}
            onOpenSettings={() => setSettingsOpen(true)}
          />

          <main className="flex min-w-0 flex-1 flex-col bg-background">
            {activeView === 'logs' ? (
              <LogsView
                onJumpToSession={handleSelectSession}
                onNote={steward.setNote}
              />
            ) : (
              <>
                <TopBar session={steward.session} onLogout={steward.logout} />

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
                  status={steward.status}
                  showReasoning={showReasoning}
                  onToggleReasoning={toggleReasoning}
                  onPermission={steward.setPermission}
                  onChange={setDraft}
                  onSend={handleSend}
                />
              </>
            )}
          </main>

          <SettingsDialog
            open={settingsOpen}
            onOpenChange={setSettingsOpen}
            provider={steward.provider}
            profiles={steward.profiles}
            user={steward.user}
            status={steward.status}
            feishu={steward.feishu}
            onSave={steward.saveProvider}
            onTest={steward.testProvider}
            onSyncModels={steward.syncModels}
            onSaveProfile={steward.saveProfile}
            onApplyProfile={steward.applyProfile}
            onDeleteProfile={steward.deleteProfile}
            onChangePassword={steward.changePassword}
            onSaveFeishu={steward.saveFeishu}
            onNote={steward.setNote}
          />
        </>
      )}
    </div>
  )
}
