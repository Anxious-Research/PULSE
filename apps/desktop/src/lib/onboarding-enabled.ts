export function isOnboardingEnabled(): boolean {
  return window.pulseDesktop?.guestOnboardingEnabled === true
}
