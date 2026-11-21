import workletUrl from './pcm-processor.js?url&no-inline'

export async function prepareCapture() {
  if (!navigator.mediaDevices?.getDisplayMedia || !window.AudioContext || !window.AudioWorkletNode) {
    throw new Error('captureUnsupported')
  }
  const context = new AudioContext({ sampleRate: 16000 })
  let display
  let microphone
  let micSource
  let node
  let closed = false

  function disableMic() {
    micSource?.disconnect()
    microphone?.getTracks().forEach((track) => track.stop())
    microphone = null
    micSource = null
  }

  function close() {
    if (closed) return
    closed = true
    disableMic()
    display?.getTracks().forEach((track) => track.stop())
    node?.disconnect()
    void context.close().catch(() => {})
  }

  try {
    const playback = context.resume().catch(() => {})
    display = await navigator.mediaDevices.getDisplayMedia({
      audio: { suppressLocalAudioPlayback: false },
      video: { displaySurface: 'browser' },
    })
    if (!display.getAudioTracks().length) throw new Error('captureNoAudio')
    await playback
    await context.audioWorklet.addModule(workletUrl)
    node = new AudioWorkletNode(context, 'meeting-pcm', {
      numberOfInputs: 2, numberOfOutputs: 1, outputChannelCount: [1],
    })
    context.createMediaStreamSource(display).connect(node, 0, 0)
  } catch (error) {
    close()
    if (error.message === 'captureNoAudio') throw error
    throw new Error('captureDenied')
  }

  async function enableMic(onEnded) {
    if (microphone) return
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    if (closed) {
      stream.getTracks().forEach((track) => track.stop())
      return
    }
    microphone = stream
    micSource = context.createMediaStreamSource(stream)
    micSource.connect(node, 0, 1)
    const [track] = stream.getAudioTracks()
    track.addEventListener('ended', () => { disableMic(); onEnded?.() }, { once: true })
  }

  function start(mid, onLost) {
    let pending = Promise.resolve()
    let queued = 0
    let stopping = false
    let flushed
    const lost = () => {
      if (closed || stopping) return
      close()
      onLost()
    }
    display.getTracks().forEach((track) => track.addEventListener('ended', lost, { once: true }))
    node.port.onmessage = ({ data }) => {
      if (data === 'flushed') { flushed?.(); return }
      if (closed) return
      if (queued >= 25 && !stopping) { lost(); return }
      queued += 1
      pending = pending.then(async () => {
        if (closed) return
        const response = await fetch(`/api/meeting/${mid}/capture`, {
          method: 'POST', headers: { 'Content-Type': 'application/octet-stream' },
          body: data, signal: AbortSignal.timeout(5000),
        })
        if (!response.ok) lost()
      }).catch(lost).finally(() => { queued -= 1 })
    }
    if (display.getTracks().some((track) => track.readyState === 'ended')) queueMicrotask(lost)
    else node.connect(context.destination)
    return {
      close,
      enableMic,
      disableMic,
      async stop() {
        if (closed || stopping) return
        stopping = true
        await new Promise((resolve) => {
          const timer = setTimeout(resolve, 500)
          flushed = () => { clearTimeout(timer); resolve() }
          node.port.postMessage('flush')
        })
        node.port.onmessage = null
        const deadline = AbortSignal.timeout(6000)
        await Promise.race([pending, new Promise((resolve) => deadline.addEventListener('abort', resolve, { once: true }))])
        close()
      },
    }
  }

  return { start, close, enableMic }
}
