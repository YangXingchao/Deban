export function isImeKey(event) {
  const key = event.nativeEvent ?? event
  // The key that commits an IME candidate may arrive after compositionend.
  return key.isComposing || key.keyCode === 229
}
