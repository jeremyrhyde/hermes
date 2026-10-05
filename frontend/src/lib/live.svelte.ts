import { LiveClient, wsUrl } from './live';

export const liveState = $state({ connected: false });

export const live = new LiveClient({
  url: wsUrl(document.baseURI),
  onStatus: (connected) => {
    liveState.connected = connected;
  },
});
