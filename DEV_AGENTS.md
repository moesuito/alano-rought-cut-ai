# DEV_AGENTS.md — Guia do Desenvolvedor e Instruções para Agentes de IA

> **Documento Oficial de Engenharia para Agentes de IA (Codex, Claude Code, Antigravity, etc.) e Desenvolvedores**  
> **Versão do Projeto:** v0.6.0 (Autonomous Agentic Loop Edition)  
> **Repositório:** `moesuito/alano-rought-cut-ai`

---

## 1. Identidade e Propósito do Projeto

O **Alano Rough Cut AI** é um motor autônomo especializado em **Rough Cut (Primeiro Corte Bruto)** para vídeos de criadores, videoaulas, tutoriais e redes sociais.

* **Entrega Final:** Arquivo XML no padrão Final Cut Pro 7 (`timeline.xml`), 100% compatível e pronto para ser importado diretamente no **Adobe Premiere Pro**.
* **Escopo Estrito (Hard Scope):** Apenas montagem e decupagem da timeline. O sistema **NÃO** faz render final de vídeo MP4, legendas, motion design, color grading, trilha sonora ou animações.

---

## 2. Onde Fica Cada Coisa (Estrutura de Pastas e Builds)

### 📍 2.1 Build Instalada no Sistema (Produção Local do Usuário)
* **Caminho:** `%APPDATA%\alano-rought-cut-ai` (Ex: `C:\Users\Alano\AppData\Roaming\alano-rought-cut-ai`)
* **O que contém:**
  * `bin/alanocut.cmd` e `bin/alanocut.ps1` (adicionados ao `PATH` do Windows, permitindo rodar `alanocut` em qualquer terminal).
  * `helpers/` (todo o código Python do pipeline, ASR, agente e XML).
  * `.agents/` (documentos do protocolo agêntico).
  * `docs/` (arquitetura e referências técnicas).
  * `config.json` (configurações do workspace).
* ⚠️ **REGRA CRÍTICA PARA AGENTES DE DEV:** Sempre que você fizer alterações de código no repositório de desenvolvimento, você **DEVE sincronizar os arquivos para `%APPDATA%\alano-rought-cut-ai`** para que o usuário possa testar a nova build imediatamente no terminal.

### 📍 2.2 Sessões e Caches de Execução (Zero Folder Pollution)
* **Caminho:** `%APPDATA%\AlanoCut\sessions\<session_id>\`
* **O que contém:**
  * `transcripts/` (transcrições `.json` geradas e alinhadas por áudio).
  * `takes_packed.md` (leitura condensada de frases e pausas).
  * `editorial_strategy.json` (estratégia diagnosticada na Task 1 do agente).
  * `edl.json` e `refine_report.json` (EDL refinada pelo Snapper).
  * `preview.wav` e `preview_timeline.json` (áudio PCM para controle de qualidade).
  * `editorial_audit.txt` (relatório human-readable com justificativas e ciclos de auto-reflexão).
  * `session.log` (log cronológico técnico de execução).
  * `timeline.xml` (cópia da timeline entregue).

### 📍 2.3 Repositório de Desenvolvimento
* **Caminho:** `O:\Antigravity\alano-cut` (e seus worktrees em `.worktrees/`).

---

## 3. Arquitetura do Sistema (Como o Alano Cut Funciona)

O pipeline executa 3 fases complementares:

```
[Mídia Bruta .mov / .mp4]
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 🟢 FASE 1: STACK ACÚSTICA LOCAL NA GPU (Zero Tokens)        │
│ 1. Denoising: DeepFilterNet 3 (redução de 100 dB)           │
│ 2. ASR: Whisper Large v3 Turbo (GPU Vulkan)                 │
│ 3. Forced Alignment: Wav2Vec2 CTC DirectML (timestamps ms)  │
│ 4. Diarization: Pyannote Diarization v3 (ONNX DirectML)      │
│ 5. Pack: helpers/pack_transcripts.py -> takes_packed.md     │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 🟡 FASE 2: MOTOR AGÊNTICO EM LOOPS (helpers/agentic_editor) │
│ Task 1: Diagnóstico Global, Retakes e Estratégia Narrativa │
│ Task 2: Decupagem e Montagem da EDL Preliminar              │
│ Task 3: Auto-Reflexão Crítica e Re-corte em Loops           │
│         -> Conclui dinamicamente com status: "APPROVED"     │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 🔵 FASE 3: REFINAMENTO ACÚSTICO E EXPORTAÇÃO (Zero Tokens)   │
│ 1. helpers/refine_edl_boundaries.py (Snapper + 66ms padding)│
│ 2. helpers/render.py & preview_audio_qc.py (Preview WAV QC) │
│ 3. helpers/edl_to_fcpxml.py (Gera ./timeline.xml)           │
│ 4. helpers/session_manager.py (Exporta entrega limpa)       │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Regras e Protocolos de Desenvolvimento para Agentes

Ao trabalhar neste repositório, siga rigorosamente as seguintes diretrizes:

### 1. Invariantes Editoriais Inquebráveis
* **Nunca corte dentro de palavras (`never cut inside a word`):** Os cortes devem respeitar os limites de palavras e o padding acústico de $\ge 66$ms aplicado pelo Snapper.
* **Preservação de Contexto vs. Cacos:** Termos como *"Beleza."*, *"Tá."*, *"Corta."*, *"Volta."* isolados entre pausas e erros são **cacos de bastidor** e devem ser eliminados; se usados com função comunicativa (ex: *"Tudo beleza pessoal?"*), devem ser **mantidos como conteúdo**.
* **Pacing Diferenciado:**
  * **Vídeos Longos (Aulas/Tutoriais):** Gap padrão de 350ms. Listas e enumerações usam `is_list: true` para preservar pausas naturais de até 500ms.
  * **Vídeos Curtos (Reels/TikTok):** Gap padrão de 200ms, sem filtro de lista, duração estrita $\le 90$s.

### 2. Protocolo de Testes Automatizados
* **Comando:** `py -3.12 -m pytest -q`
* **Regra:** Nunca commite ou conclua uma tarefa com testes falhando. Todos os 275+ testes da suíte DEVEM passar com sucesso.

### 3. Protocolo de Sincronização Local (Deploy em AppData)
Após editar qualquer arquivo em `helpers/`, `bin/`, `docs/`, prompts ou templates, execute no PowerShell:

```powershell
$Src = "O:\Antigravity\alano-cut\.worktrees\codex-feature-v0.4-audio-snapper"
$Dest = Join-Path $env:APPDATA "alano-rought-cut-ai"

Copy-Item -Path (Join-Path $Src "helpers") -Destination $Dest -Recurse -Force
Copy-Item -Path (Join-Path $Src "bin") -Destination $Dest -Recurse -Force
Copy-Item -Path (Join-Path $Src ".agents") -Destination $Dest -Recurse -Force
Copy-Item -Path (Join-Path $Src "docs") -Destination $Dest -Recurse -Force
Copy-Item -Path (Join-Path $Src "config.json") -Destination $Dest -Force
Copy-Item -Path (Join-Path $Src "README.md") -Destination $Dest -Force
Copy-Item -Path (Join-Path $Src "CHANGELOG.md") -Destination $Dest -Force
```

### 4. Protocolo Git
* Crie branches semânticas para novas features (ex: `codex/feature-v0.6.0-agentic-editor`).
* Faça commits claros e objetivos e faça push para a branch correspondente no repositório remoto.

---

## 5. Próximas Frentes e Roadmap para o Próximo Agente

1. **Modo 2 — Batch Multi-Video Dispatcher:**
   - Implementar a partição de lotes de múltiplos vídeos a partir de gravações brutas longas (ex: criar 5 Reels independentes ou 2 aulas separadas de um lote de 10 arquivos).
   - O Dispatcher divide os micro-briefs e executa o `AgenticEditorialLoop` isoladamente para cada vídeo em um contexto limpo.
2. **Refinamento dos Prompts de Auto-Reflexão:**
   - Acompanhar os relatórios de `editorial_audit.txt` em projetos reais para aprimorar os critérios de inspeção e auto-correção da Task 3.
3. **Integração com Premiere via CEP / ExtendScript:**
   - Futuras expansões para importar automaticamente a timeline no Premiere Pro aberto via MCP ou extensão.
