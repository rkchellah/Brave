import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import {
  View, Text, StyleSheet, TouchableOpacity,
  ScrollView, RefreshControl,
} from 'react-native';
import { useState, useEffect, useCallback } from 'react';
import { initializeApp, getApps } from 'firebase/app';
import { getDatabase, ref, onValue, set, update } from 'firebase/database';

const FIREBASE_CONFIG = {
  apiKey: "AIzaSyD-XejoT5DumL1DCM7v22CdQepJ6hbnE_M",
  authDomain: "thunder-23e63.firebaseapp.com",
  databaseURL: "https://thunder-23e63-default-rtdb.firebaseio.com",
  projectId: "thunder-23e63",
  storageBucket: "thunder-23e63.firebasestorage.app",
  messagingSenderId: "58146334585",
  appId: "1:58146334585:web:ef38eb25b1f3bc863ef1a8",
};
const USER_ID = "RcB4T6930SVvE4Lt9mCSs6nbG1G2";

const app = getApps().length === 0 ? initializeApp(FIREBASE_CONFIG) : getApps()[0];
const db = getDatabase(app);

const Tab = createBottomTabNavigator();

const C = {
  bg: '#0D0D0F',
  surface: '#16181C',
  surface2: '#1E2026',
  border: '#222428',
  primary: '#4F8EF7',
  success: '#22C55E',
  danger: '#EF4444',
  warning: '#F59E0B',
  tPrim: '#F1F1F1',
  tSec: '#8A8F98',
  tMuted: '#4A4F5A',
};

const fmt$ = (n) => `$${(n ?? 0).toLocaleString('en', { minimumFractionDigits: 2 })}`;
const fmtTime = (iso) => iso ? new Date(iso).toLocaleTimeString() : '—';
const fmtDate = (iso) => iso ? new Date(iso).toLocaleString() : '—';

function DashboardScreen() {
  const [status, setStatus] = useState(null);
  const [braveConfig, setBraveConfig] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    const u1 = onValue(ref(db, `users/${USER_ID}/bot_status`), s => setStatus(s.val()));
    const u2 = onValue(ref(db, `users/${USER_ID}/brave_config`), s => setBraveConfig(s.val()));
    return () => { u1(); u2(); };
  }, []);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    setTimeout(() => setRefreshing(false), 800);
  }, []);

  const sendCommand = async (action) => {
    await set(ref(db, `users/${USER_ID}/commands`), {
      action,
      timestamp: new Date().toISOString(),
    });
  };

  const toggleMode = async (manual) => {
    await update(ref(db, `users/${USER_ID}/brave_config`), {
      execution_mode: manual ? 'MANUAL' : 'AUTO',
    });
  };

  const isRunning = status?.is_running === true;
  const isPaused = status?.paused_reason === 'DAILY_LOSS_LIMIT';
  const isManual = String(braveConfig?.execution_mode ?? 'AUTO').toUpperCase() === 'MANUAL';
  const balance = status?.balance ?? 0;
  const equity = status?.equity ?? 0;
  const profit = status?.profit ?? 0;
  const positions = status?.open_positions ?? 0;
  const strategy = status?.active_strategy ?? '—';
  const botColor = isPaused ? C.warning : isRunning ? C.success : C.tMuted;
  const botLabel = isPaused ? 'PAUSED' : isRunning ? 'RUNNING' : 'STOPPED';
  const pnlColor = profit >= 0 ? C.success : C.danger;

  return (
    <ScrollView
      style={s.screen}
      contentContainerStyle={s.scroll}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
    >
      <View style={s.header}>
        <Text style={s.headerTitle}>Brave</Text>
        <View style={[s.pill, { borderColor: botColor, backgroundColor: botColor + '22' }]}>
          <View style={[s.dot, { backgroundColor: botColor }]} />
          <Text style={[s.pillTxt, { color: botColor }]}>{botLabel}</Text>
        </View>
      </View>

      <View style={s.row}>
        <View style={[s.card, s.half]}>
          <Text style={s.lbl}>Balance</Text>
          <Text style={s.val}>{fmt$(balance)}</Text>
        </View>
        <View style={[s.card, s.half]}>
          <Text style={s.lbl}>Equity</Text>
          <Text style={s.val}>{fmt$(equity)}</Text>
        </View>
      </View>
      <View style={s.row}>
        <View style={[s.card, s.half]}>
          <Text style={s.lbl}>Open P&L</Text>
          <Text style={[s.val, { color: pnlColor }]}>
            {profit >= 0 ? '+' : ''}{fmt$(profit)}
          </Text>
        </View>
        <View style={[s.card, s.half]}>
          <Text style={s.lbl}>Positions</Text>
          <Text style={s.val}>{String(positions)}</Text>
        </View>
      </View>

      <View style={s.card}>
        <View style={s.cardRow}>
          <Text style={s.lbl}>Strategy</Text>
          <Text style={[s.valSec, { textTransform: 'capitalize' }]}>{strategy}</Text>
        </View>
        <View style={[s.cardRow, { marginTop: 10 }]}>
          <Text style={s.lbl}>Open markets</Text>
          <Text style={s.valSec}>{status?.open_markets?.join(', ') || 'None'}</Text>
        </View>
        <View style={[s.cardRow, { marginTop: 10 }]}>
          <Text style={s.lbl}>Last updated</Text>
          <Text style={s.valSec}>{fmtTime(status?.last_updated)}</Text>
        </View>
      </View>

      <View style={s.card}>
        <Text style={s.lbl}>Execution mode</Text>
        <View style={[s.row, { marginTop: 10, marginBottom: 0 }]}>
          <TouchableOpacity
            style={[s.btn, s.half, {
              borderColor: !isManual ? C.primary : C.border,
              backgroundColor: !isManual ? C.primary + '22' : 'transparent',
            }]}
            onPress={() => toggleMode(false)}
          >
            <Text style={[s.btnTxt, { color: !isManual ? C.primary : C.tMuted }]}>AUTO</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[s.btn, s.half, {
              borderColor: isManual ? C.warning : C.border,
              backgroundColor: isManual ? C.warning + '22' : 'transparent',
            }]}
            onPress={() => toggleMode(true)}
          >
            <Text style={[s.btnTxt, { color: isManual ? C.warning : C.tMuted }]}>MANUAL</Text>
          </TouchableOpacity>
        </View>
        <Text style={[s.valSec, { marginTop: 10 }]}>
          {isManual ? 'You confirm each trade before execution' : 'Bot executes signals immediately'}
        </Text>
      </View>

      {isPaused ? (
        <View style={s.warnBanner}>
          <Text style={s.warnTxt}>
            {'⚠ Daily loss limit hit — bot paused until tomorrow.'}
          </Text>
        </View>
      ) : null}

      <View style={s.row}>
        <TouchableOpacity
          style={[s.btn, s.half, { borderColor: C.success, backgroundColor: C.success + '22' }]}
          onPress={() => sendCommand('start')}
        >
          <Text style={[s.btnTxt, { color: C.success }]}>{'▶  Start'}</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[s.btn, s.half, { borderColor: C.danger, backgroundColor: C.danger + '22' }]}
          onPress={() => sendCommand('stop')}
        >
          <Text style={[s.btnTxt, { color: C.danger }]}>{'■  Stop'}</Text>
        </TouchableOpacity>
      </View>

      <Text style={s.footer}>Commands sent via Firebase — bot responds within 60s</Text>
    </ScrollView>
  );
}

function SignalsScreen() {
  const [signals, setSignals] = useState({});
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const u = onValue(ref(db, `users/${USER_ID}/pending_signals`), s => setSignals(s.val() ?? {}));
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => { u(); clearInterval(tick); };
  }, []);

  const respond = async (key, response) => {
    await update(ref(db, `users/${USER_ID}/pending_signals/${key}`), {
      status: response,
      responded_at: new Date().toISOString(),
    });
  };

  const pendingList = Object.entries(signals)
    .filter(([, v]) => v.status === 'PENDING')
    .sort(([, a], [, b]) => (b.pushed_at > a.pushed_at ? 1 : -1));

  const historyList = Object.entries(signals)
    .filter(([, v]) => v.status !== 'PENDING')
    .sort(([, a], [, b]) => (b.pushed_at > a.pushed_at ? 1 : -1))
    .slice(0, 10);

  const timeLeft = (expiresAt) => {
    const secs = Math.max(0, Math.floor(expiresAt - now / 1000));
    return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
  };

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.scroll}>
      <Text style={s.screenTitle}>Signals</Text>
      {pendingList.length === 0 ? (
        <View style={s.emptyCard}>
          <Text style={s.emptyIcon}>📡</Text>
          <Text style={s.emptyTxt}>No pending signals</Text>
          <Text style={s.emptyHint}>Switch to MANUAL on Dashboard to confirm trades before execution</Text>
        </View>
      ) : null}
      {pendingList.map(([key, sig]) => {
        const dc = sig.direction === 'BUY' ? C.success : C.danger;
        return (
          <View key={key} style={[s.card, { borderColor: C.warning, borderWidth: 1 }]}>
            <View style={s.cardRow}>
              <Text style={s.val}>{sig.symbol}</Text>
              <View style={[s.pill, { borderColor: dc, backgroundColor: dc + '22' }]}>
                <Text style={[s.pillTxt, { color: dc }]}>{sig.direction}</Text>
              </View>
            </View>
            <View style={[s.cardRow, { marginTop: 10 }]}>
              <Text style={s.lbl}>Entry</Text>
              <Text style={s.valSec}>{String(sig.entry_price ?? '')}</Text>
            </View>
            <View style={[s.cardRow, { marginTop: 6 }]}>
              <Text style={s.lbl}>SL / TP</Text>
              <Text style={s.valSec}>{String(sig.suggested_sl ?? '')} / {String(sig.suggested_tp ?? '')}</Text>
            </View>
            <View style={[s.cardRow, { marginTop: 6 }]}>
              <Text style={s.lbl}>R:R</Text>
              <Text style={s.valSec}>{String(sig.risk_reward_ratio ?? '')}R</Text>
            </View>
            <View style={[s.cardRow, { marginTop: 6 }]}>
              <Text style={s.lbl}>Expires in</Text>
              <Text style={[s.valSec, { color: C.warning }]}>{timeLeft(sig.expires_at)}</Text>
            </View>
            <View style={[s.row, { marginTop: 14, marginBottom: 0 }]}>
              <TouchableOpacity
                style={[s.btn, s.half, { borderColor: C.success, backgroundColor: C.success + '22' }]}
                onPress={() => respond(key, 'CONFIRMED')}
              >
                <Text style={[s.btnTxt, { color: C.success }]}>{'✓  Confirm'}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[s.btn, s.half, { borderColor: C.danger, backgroundColor: C.danger + '22' }]}
                onPress={() => respond(key, 'REJECTED')}
              >
                <Text style={[s.btnTxt, { color: C.danger }]}>{'✕  Reject'}</Text>
              </TouchableOpacity>
            </View>
          </View>
        );
      })}
      {historyList.length > 0 ? (
        <View>
          <Text style={[s.sectionTitle, { marginTop: 20 }]}>Recent</Text>
          {historyList.map(([key, sig]) => {
            const sc = sig.status === 'CONFIRMED' ? C.success : sig.status === 'REJECTED' ? C.danger : C.tMuted;
            return (
              <View key={key} style={s.card}>
                <View style={s.cardRow}>
                  <Text style={s.valSec}>{sig.symbol} {sig.direction}</Text>
                  <Text style={[s.valSec, { color: sc }]}>{sig.status}</Text>
                </View>
                <Text style={[s.lbl, { marginTop: 4 }]}>{fmtDate(sig.pushed_at)}</Text>
              </View>
            );
          })}
        </View>
      ) : null}
    </ScrollView>
  );
}

function InsightsScreen() {
  const [sentiment, setSentiment] = useState({});
  const PAIRS = ['EURUSD', 'GBPUSD', 'XAUUSD'];

  useEffect(() => {
    const u = onValue(ref(db, `users/${USER_ID}/sentiment`), s => setSentiment(s.val() ?? {}));
    return () => u();
  }, []);

  const biasColor = (b) => ({ BULLISH: C.success, BEARISH: C.danger, NEUTRAL: C.tMuted }[b] ?? C.tMuted);

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.scroll}>
      <Text style={s.screenTitle}>AI Insights</Text>
      <Text style={s.subtitle}>GPT-4 news + Grok social · updates every 15 min</Text>
      {PAIRS.map(pair => {
        const data = sentiment[pair];
        if (!data) {
          return (
            <View key={pair} style={s.card}>
              <Text style={s.val}>{pair}</Text>
              <Text style={[s.lbl, { marginTop: 6 }]}>Waiting for sentiment data...</Text>
            </View>
          );
        }
        const bc = biasColor(data.direction_bias);
        const score = data.score ?? 0;
        const pct = String(Math.round(((score + 1) / 2) * 100)) + '%';
        const barColor = score > 0.15 ? C.success : score < -0.15 ? C.danger : C.tMuted;
        const ac = data.trade_alignment === 'ALIGNED' ? C.success : data.trade_alignment === 'OPPOSED' ? C.danger : C.tMuted;
        return (
          <View key={pair} style={s.card}>
            <View style={s.cardRow}>
              <Text style={s.val}>{pair}</Text>
              <View style={[s.pill, { borderColor: bc, backgroundColor: bc + '22' }]}>
                <Text style={[s.pillTxt, { color: bc }]}>{data.direction_bias}</Text>
              </View>
            </View>
            <View style={[s.barBg, { marginTop: 10 }]}>
              <View style={[s.barFill, { width: pct, backgroundColor: barColor }]} />
            </View>
            <Text style={[s.lbl, { marginTop: 4, textAlign: 'center' }]}>
              Score {score.toFixed(2)} · {data.confidence} confidence
            </Text>
            <View style={[s.cardRow, { marginTop: 10 }]}>
              <Text style={s.lbl}>Signal alignment</Text>
              <Text style={[s.valSec, { color: ac }]}>{data.trade_alignment ?? '—'}</Text>
            </View>
            {data.gpt4_summary ? (
              <View style={[s.infoBanner, { marginTop: 10 }]}>
                <Text style={[s.lbl, { marginBottom: 4 }]}>📰 News</Text>
                <Text style={s.infoTxt}>{data.gpt4_summary}</Text>
              </View>
            ) : null}
            {data.risk_advisory ? (
              <View style={[s.warnBanner, { marginTop: 8 }]}>
                <Text style={s.warnTxt}>{'⚠ ' + data.risk_advisory}</Text>
              </View>
            ) : null}
            <Text style={[s.lbl, { marginTop: 8 }]}>Updated {fmtTime(data.updated_at)}</Text>
          </View>
        );
      })}
    </ScrollView>
  );
}

function AlertsScreen() {
  const [alerts, setAlerts] = useState({});

  useEffect(() => {
    const u = onValue(ref(db, `users/${USER_ID}/alerts`), s => setAlerts(s.val() ?? {}));
    return () => u();
  }, []);

  const list = Object.entries(alerts)
    .sort(([, a], [, b]) => (b.sent_at > a.sent_at ? 1 : -1))
    .slice(0, 30);

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.scroll}>
      <Text style={s.screenTitle}>Alerts</Text>
      {list.length === 0 ? (
        <View style={s.emptyCard}>
          <Text style={s.emptyIcon}>🔔</Text>
          <Text style={s.emptyTxt}>No alerts yet</Text>
          <Text style={s.emptyHint}>Trade alerts appear here when the bot executes signals</Text>
        </View>
      ) : null}
      {list.map(([key, alert]) => {
        const dc = alert.direction === 'BUY' ? C.success : C.danger;
        return (
          <View key={key} style={s.card}>
            <View style={s.cardRow}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Text style={s.val}>{alert.symbol}</Text>
                <View style={[s.pill, { borderColor: dc, backgroundColor: dc + '22' }]}>
                  <Text style={[s.pillTxt, { color: dc }]}>{alert.direction}</Text>
                </View>
              </View>
              <Text style={s.lbl}>{fmtTime(alert.sent_at)}</Text>
            </View>
            <View style={[s.cardRow, { marginTop: 8 }]}>
              <Text style={s.lbl}>Entry</Text>
              <Text style={s.valSec}>{String(alert.entry_price ?? '')}</Text>
            </View>
            <View style={[s.cardRow, { marginTop: 4 }]}>
              <Text style={s.lbl}>SL / TP</Text>
              <Text style={s.valSec}>{String(alert.suggested_sl ?? '')} / {String(alert.suggested_tp ?? '')}</Text>
            </View>
            <View style={[s.cardRow, { marginTop: 4 }]}>
              <Text style={s.lbl}>R:R</Text>
              <Text style={s.valSec}>{String(alert.risk_reward_ratio ?? '')}R</Text>
            </View>
          </View>
        );
      })}
    </ScrollView>
  );
}

function SettingsScreen() {
  const [braveConfig, setBraveConfig] = useState(null);
  const [health, setHealth] = useState(null);

  useEffect(() => {
    const u1 = onValue(ref(db, `users/${USER_ID}/brave_config`), s => setBraveConfig(s.val()));
    const u2 = onValue(ref(db, `users/${USER_ID}/health`), s => setHealth(s.val()));
    return () => { u1(); u2(); };
  }, []);

  const switchStrategy = async (strategy) => {
    await update(ref(db, `users/${USER_ID}/brave_config`), {
      active_strategy: strategy,
      last_switched: new Date().toISOString(),
      switched_by: 'mobile',
    });
  };

  const strategies = braveConfig?.available_strategies ?? ['thunder'];
  const active = braveConfig?.active_strategy ?? 'thunder';
  const hc = health?.status === 'HEALTHY' ? C.success : health?.status === 'DEGRADED' ? C.warning : C.tMuted;

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.scroll}>
      <Text style={s.screenTitle}>Settings</Text>
      <Text style={s.sectionTitle}>Strategy</Text>
      {strategies.map(strat => (
        <TouchableOpacity
          key={strat}
          style={[s.card, active === strat ? { borderColor: C.primary, borderWidth: 1 } : {}]}
          onPress={() => switchStrategy(strat)}
        >
          <View style={s.cardRow}>
            <Text style={[s.valSec, { textTransform: 'capitalize' }]}>{strat}</Text>
            {active === strat ? (
              <View style={[s.pill, { borderColor: C.primary, backgroundColor: C.primary + '22' }]}>
                <Text style={[s.pillTxt, { color: C.primary }]}>ACTIVE</Text>
              </View>
            ) : null}
          </View>
        </TouchableOpacity>
      ))}
      <Text style={[s.sectionTitle, { marginTop: 20 }]}>System health</Text>
      <View style={s.card}>
        <View style={s.cardRow}>
          <Text style={s.lbl}>Status</Text>
          <Text style={[s.valSec, { color: hc }]}>{health?.status ?? '—'}</Text>
        </View>
        <View style={[s.cardRow, { marginTop: 8 }]}>
          <Text style={s.lbl}>MT5 connected</Text>
          <Text style={[s.valSec, { color: health?.mt5_connected === true ? C.success : C.danger }]}>
            {health?.mt5_connected === true ? 'Yes' : 'No'}
          </Text>
        </View>
        <View style={[s.cardRow, { marginTop: 8 }]}>
          <Text style={s.lbl}>Account trading</Text>
          <Text style={[s.valSec, { color: health?.account_trade_allowed === true ? C.success : C.danger }]}>
            {health?.account_trade_allowed === true ? 'Allowed' : 'Restricted'}
          </Text>
        </View>
        <Text style={[s.lbl, { marginTop: 8 }]}>Updated {fmtTime(health?.timestamp)}</Text>
      </View>
      <Text style={[s.footer, { marginTop: 16 }]}>Brave v2.0 · Strategy changes apply within 60s</Text>
    </ScrollView>
  );
}

const TAB_ICONS = { Dashboard: '◉', Signals: '📡', Insights: '🧠', Alerts: '🔔', Settings: '⚙️' };

export default function App() {
  return (
    <NavigationContainer>
      <Tab.Navigator
        screenOptions={({ route }) => ({
          headerShown: false,
          tabBarStyle: { backgroundColor: C.surface, borderTopColor: C.border, height: 62, paddingBottom: 8 },
          tabBarActiveTintColor: C.primary,
          tabBarInactiveTintColor: C.tMuted,
          tabBarLabelStyle: { fontSize: 10, marginTop: 2 },
          tabBarIcon: ({ focused }) => (
            <Text style={{ fontSize: 18, opacity: focused ? 1 : 0.4 }}>{TAB_ICONS[route.name] ?? '●'}</Text>
          ),
        })}
      >
        <Tab.Screen name="Dashboard" component={DashboardScreen} />
        <Tab.Screen name="Signals" component={SignalsScreen} />
        <Tab.Screen name="Insights" component={InsightsScreen} />
        <Tab.Screen name="Alerts" component={AlertsScreen} />
        <Tab.Screen name="Settings" component={SettingsScreen} />
      </Tab.Navigator>
    </NavigationContainer>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.bg },
  scroll: { padding: 16, paddingBottom: 40 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, marginTop: 8 },
  headerTitle: { fontSize: 26, fontWeight: '700', color: C.tPrim },
  screenTitle: { fontSize: 22, fontWeight: '700', color: C.tPrim, marginBottom: 4, marginTop: 8 },
  sectionTitle: { fontSize: 13, fontWeight: '600', color: C.tSec, marginBottom: 10, letterSpacing: 0.5, textTransform: 'uppercase' },
  subtitle: { fontSize: 12, color: C.tMuted, marginBottom: 16 },
  pill: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20, borderWidth: 1, gap: 5 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  pillTxt: { fontSize: 11, fontWeight: '600', letterSpacing: 0.5 },
  row: { flexDirection: 'row', gap: 12, marginBottom: 12 },
  half: { flex: 1 },
  card: { backgroundColor: C.surface, borderRadius: 14, borderWidth: 1, borderColor: C.border, padding: 16, marginBottom: 12 },
  cardRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  lbl: { fontSize: 11, color: C.tSec, textTransform: 'uppercase', letterSpacing: 0.5 },
  val: { fontSize: 20, fontWeight: '700', color: C.tPrim },
  valSec: { fontSize: 13, color: C.tPrim },
  btn: { flex: 1, paddingVertical: 14, borderRadius: 12, borderWidth: 1, alignItems: 'center' },
  btnTxt: { fontSize: 15, fontWeight: '600' },
  infoBanner: { backgroundColor: C.primary + '15', borderRadius: 8, padding: 10, borderLeftWidth: 3, borderLeftColor: C.primary },
  infoTxt: { fontSize: 12, color: C.tPrim, lineHeight: 18 },
  warnBanner: { backgroundColor: C.warning + '18', borderRadius: 8, padding: 10, borderLeftWidth: 3, borderLeftColor: C.warning },
  warnTxt: { fontSize: 12, color: C.warning, lineHeight: 18 },
  barBg: { height: 6, backgroundColor: C.border, borderRadius: 3, overflow: 'hidden' },
  barFill: { height: 6, borderRadius: 3 },
  emptyCard: { alignItems: 'center', padding: 40, gap: 8 },
  emptyIcon: { fontSize: 40 },
  emptyTxt: { fontSize: 16, fontWeight: '600', color: C.tPrim },
  emptyHint: { fontSize: 12, color: C.tMuted, textAlign: 'center', lineHeight: 18 },
  footer: { textAlign: 'center', color: C.tMuted, fontSize: 11, marginTop: 8 },
});