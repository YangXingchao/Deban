class MeetingPCM extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buffer = new ArrayBuffer(3200 * 4)
    this.samples = new DataView(this.buffer)
    this.offset = 0
    this.port.onmessage = () => {
      this.send()
      this.port.postMessage('flushed')
    }
  }

  send() {
    if (!this.offset) return
    const data = this.offset === this.buffer.byteLength ? this.buffer : this.buffer.slice(0, this.offset)
    this.port.postMessage(data, [data])
    this.buffer = new ArrayBuffer(3200 * 4)
    this.samples = new DataView(this.buffer)
    this.offset = 0
  }

  process(inputs, outputs) {
    for (let index = 0; index < outputs[0][0].length; index += 1) {
      for (const channels of inputs) {
        let sample = 0
        for (const channel of channels) sample += channel[index]
        sample /= channels.length || 1
        this.samples.setInt16(this.offset, Math.max(-32768, Math.min(32767, sample * 32768)), true)
        this.offset += 2
      }
      if (this.offset === this.buffer.byteLength) this.send()
    }
    return true
  }
}

registerProcessor('meeting-pcm', MeetingPCM)
