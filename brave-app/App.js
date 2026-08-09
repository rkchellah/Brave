import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import React, { useState, useEffect, useCallback, useMemo, memo, useRef } from 'react';
import {
  StyleSheet, Text, View, ScrollView, TouchableOpacity,
  RefreshControl, ActivityIndicator, TextInput,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { initializeApp, getApps } from 'firebase/app';
import { getDatabase, ref, onValue, set, update } from 'firebase/database';

// ── Firebase config ────────────────────────────────────────────────
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

const firebaseApp = getApps().length === 0 ? initializeApp(FIREBASE_CONFIG) : getApps()[0];
const db = getDatabase(firebaseApp);

const Tab = createBottomTabNavigator();

// ── Design tokens ─────────────────────────────────────────────────
const theme = {
  bg:            '#0B0E14',
  surface:       '#141824',
  surfaceRaised: '#1B2130',
  textPrimary:   '#F5F6F8',
  textSecondary: '#8B93A7',
  textMuted:     '#565E70',
  success:       '#2ECC8F',
  danger:        '#EF5D6F',
  accent:        '#5B8DEF',
  radius: { sm: 8, md: 14, lg: 20 },
  spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 },
};

const TINT = {
  success: 'rgba(46,204,143,0.16)',
  danger:  'rgba(239,93,111,0.16)',
  accent:  'rgba(91,141,239,0.16)',
};

// ── Helpers ────────────────────────────────────────────────────────
const DASH = '—';

const isNum = (n) => typeof n === 'number' && Number.isFinite(n);

const fmt$ = (n) => isNum(n)
  ? `$${n.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  : DASH;

const fmtTime = (iso) => {
  if (!iso) return DASH;
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? DASH
    : d.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit' });
};

const byNewest = (getStamp) => (a, b) => String(getStamp(b) ?? '').localeCompare(String(getStamp(a) ?? ''));

/**
 * Subscribe to a Firebase path.
 *
 * Returns [value, error]. Firebase's onValue reports permission and network
 * failures through its error callback — without one, a dropped connection
 * leaves every screen silently stuck on stale data.
 */
function useFirebaseValue(path, fallback = null) {
  const [value, setValue] = useState(fallback);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    let unsubscribe = () => {};

    try {
      unsubscribe = onValue(
        ref(db, path),
        (snap) => {
          if (!active) return;
          setError(null);
          const val = snap.val();
          setValue(val === null || val === undefined ? fallback : val);
        },
        (err) => {
          if (!active) return;
          console.warn(`[firebase] ${path}:`, err?.message);
          setError(err?.message || 'Connection lost');
        },
      );
    } catch (e) {
      console.warn(`[firebase] subscribe failed for ${path}:`, e?.message);
      setError(e?.message || 'Could not subscribe');
    }

    return () => { active = false; unsubscribe(); };
  }, [path]);

  return [value, error];
}

async function writeToDb(action, describe) {
  try {
    await action();
    return null;
  } catch (e) {
    const message = e?.message || 'Network error';
    console.warn(`[firebase] ${describe} failed:`, message);
    return `${describe} failed — ${message}`;
  }
}

// ── Pair display ───────────────────────────────────────────────────
const PAIR_FLAGS = {
  EURUSD: ['🇪🇺', '🇺🇸'],
  GBPUSD: ['🇬🇧', '🇺🇸'],
  USDJPY: ['🇺🇸', '🇯🇵'],
  AUDUSD: ['🇦🇺', '🇺🇸'],
  USDCAD: ['🇺🇸', '🇨🇦'],
  USDCHF: ['🇺🇸', '🇨🇭'],
  EURCHF: ['🇪🇺', '🇨🇭'],
  EURGBP: ['🇪🇺', '🇬🇧'],
  NZDUSD: ['🇳🇿', '🇺🇸'],
};

const PAIR_NAMES = {
  EURUSD: 'Euro / U.S. Dollar',
  GBPUSD: 'British Pound / U.S. Dollar',
  USDJPY: 'U.S. Dollar / Japanese Yen',
  AUDUSD: 'Australian Dollar / U.S. Dollar',
  USDCAD: 'U.S. Dollar / Canadian Dollar',
  USDCHF: 'U.S. Dollar / Swiss Franc',
  EURCHF: 'Euro / Swiss Franc',
  EURGBP: 'Euro / British Pound',
  NZDUSD: 'New Zealand Dollar / U.S. Dollar',
  XAUUSD: 'Gold / U.S. Dollar',
  XAGUSD: 'Silver / U.S. Dollar',
};

const normalizeSymbol = (sym) => String(sym ?? '').toUpperCase().replace(/[^A-Z]/g, '');

const getPairName = (sym) => PAIR_NAMES[normalizeSymbol(sym)] ?? null;

const PairIcon = memo(function PairIcon({ symbol, size = 44 }) {
  const s = normalizeSymbol(symbol);
  const flags = PAIR_FLAGS[s];

  if (flags) {
    const badge = {
      position: 'absolute',
      width: size * 0.65, height: size * 0.65,
      borderRadius: size, backgroundColor: theme.surfaceRaised,
      justifyContent: 'center', alignItems: 'center',
      overflow: 'hidden',
    };
    return (
      <View style={{ width: size, height: size }}>
        <View style={[badge, { top: 0, right: 0, zIndex: 1 }]}>
          <Text style={{ fontSize: size * 0.4, marginTop: -2 }}>{flags[1]}</Text>
        </View>
        <View style={[badge, { bottom: 0, left: 0, zIndex: 2 }]}>
          <Text style={{ fontSize: size * 0.4, marginTop: -2 }}>{flags[0]}</Text>
        </View>
      </View>
    );
  }

  const isMetal = s.startsWith('XAU') || s.startsWith('XAG');

  return (
    <View style={{
      width: size, height: size, borderRadius: size / 2,
      backgroundColor: isMetal ? theme.accent : theme.surfaceRaised,
      justifyContent: 'center', alignItems: 'center',
    }}>
      {isMetal
        ? <MaterialCommunityIcons name="gold" size={size * 0.5} color={theme.textPrimary} />
        : <Text style={{ color: theme.textPrimary, fontSize: size * 0.4, fontWeight: '700' }}>{s.charAt(0) || '?'}</Text>}
    </View>
  );
});

// ── Shared UI ──────────────────────────────────────────────────────
const StatusPill = memo(function StatusPill({ label, tone = 'neutral' }) {
  const map = {
    success: { bg: TINT.success, fg: theme.success },
    danger:  { bg: TINT.danger,  fg: theme.danger },
    accent:  { bg: TINT.accent,  fg: theme.accent },
    neutral: { bg: theme.surfaceRaised, fg: theme.textSecondary },
  };
  const c = map[tone] ?? map.neutral;
  return (
    <View style={[s.pill, { backgroundColor: c.bg }]}>
      <Text style={[s.pillTxt, { color: c.fg }]}>{label}</Text>
    </View>
  );
});

const Banner = memo(function Banner({ message, tone = 'danger' }) {
  if (!message) return null;
  const danger = tone === 'danger';
  return (
    <View style={[s.warnBand, { backgroundColor: danger ? TINT.danger : TINT.accent }]}>
      <Text style={[s.warnBandTxt, { color: danger ? theme.danger : theme.accent }]}>{message}</Text>
    </View>
  );
});

const EmptyState = memo(function EmptyState({ icon = 'inbox-outline', title, detail }) {
  return (
    <View style={s.emptyWrap}>
      <MaterialCommunityIcons name={icon} size={40} color={theme.textMuted} />
      <Text style={s.emptyTitle}>{title}</Text>
      {detail ? <Text style={s.emptyDetail}>{detail}</Text> : null}
    </View>
  );
});

/**
 * Counts down to a MANUAL signal's expiry.
 * expires_at is written by the bot as epoch SECONDS (UTC).
 */
const SignalTimer = memo(function SignalTimer({ expiresAt }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const expiry = Number(expiresAt);
  if (!Number.isFinite(expiry) || expiry <= 0) {
    return (
      <Text style={[s.sigSub, { marginBottom: theme.spacing.md, textAlign: 'center' }]}>
        No expiry set
      </Text>
    );
  }

  const secs = Math.max(0, Math.floor(expiry - now / 1000));
  const timeStr = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
  const expired = secs === 0;

  return (
    <Text style={[s.sigSub, {
      color: expired ? theme.danger : theme.accent,
      marginBottom: theme.spacing.md,
      textAlign: 'center',
    }]}>
      {expired ? 'Expired — the bot will discard this signal' : `Expires in ${timeStr}`}
    </Text>
  );
});

const SignalCard = memo(function SignalCard({ sigKey, sig, isExpanded, isBusy, onToggle, onRespond }) {
  const isBuy = sig.direction === 'BUY';
  const status = String(sig.status ?? 'PENDING').toUpperCase();
  const isPending = status === 'PENDING';
  const subText = getPairName(sig.symbol) || sig.strategy_name || null;

  const STATUS_TONE = {
    EXECUTED: 'success', CONFIRMED: 'success', EXECUTING: 'accent',
    REJECTED: 'neutral', EXPIRED: 'neutral', FAILED: 'danger',
  };

  return (
    <View style={s.sigCardCol}>
      <TouchableOpacity
        style={s.sigCardTop}
        onPress={() => onToggle?.(sigKey)}
        activeOpacity={0.7}
        disabled={!isPending}
      >
        <View style={s.sigLeft}>
          <PairIcon symbol={sig.symbol} size={42} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={s.sigTitle}>{sig.symbol ?? DASH}</Text>
          {subText ? <Text style={s.sigSub}>{subText}</Text> : null}
          {!isPending && (
            <View style={{ marginTop: theme.spacing.xs, alignSelf: 'flex-start' }}>
              <StatusPill
                label={sig.error ? `${status} — ${sig.error}` : status}
                tone={STATUS_TONE[status] ?? 'neutral'}
              />
            </View>
          )}
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <StatusPill label={sig.direction ?? DASH} tone={isBuy ? 'success' : 'danger'} />
          <Text style={s.sigSlTp}>Entry: {sig.entry_price ?? DASH}</Text>
          <Text style={s.sigSlTp}>SL: {sig.suggested_sl ?? DASH}, TP: {sig.suggested_tp ?? DASH}</Text>
        </View>
      </TouchableOpacity>

      {isExpanded && isPending && (
        <View style={s.sigExpanded}>
          {sig.hitl_reason ? (
            <Text style={[s.gptText, { textAlign: 'center' }]}>{sig.hitl_reason}</Text>
          ) : null}
          <SignalTimer expiresAt={sig.expires_at} />
          {isBusy ? (
            <ActivityIndicator color={theme.accent} />
          ) : (
            <View style={s.sigBtnRow}>
              <TouchableOpacity onPress={() => onRespond(sigKey, 'CONFIRMED')} style={s.sigAcceptBtn}>
                <Text style={s.sigBtnTxt}>ACCEPT</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => onRespond(sigKey, 'REJECTED')} style={s.sigRejectBtn}>
                <Text style={s.sigBtnTxt}>REJECT</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>
      )}
    </View>
  );
});

// ════════════════════════════════════════════════════════════════════
// DASHBOARD SCREEN
// ════════════════════════════════════════════════════════════════════
const DashboardScreen = memo(function DashboardScreen() {
  const [status, statusErr] = useFirebaseValue(`users/${USER_ID}/bot_status`);
  const [braveConfig, configErr] = useFirebaseValue(`users/${USER_ID}/brave_config`);
  const [refreshing, setRefreshing] = useState(false);
  const [actionError, setActionError] = useState(null);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    const id = setTimeout(() => setRefreshing(false), 800);
    return () => clearTimeout(id);
  }, []);

  const sendCommand = useCallback(async (action) => {
    setActionError(await writeToDb(
      () => set(ref(db, `users/${USER_ID}/commands`), { action, timestamp: new Date().toISOString() }),
      `${action.toUpperCase()} command`,
    ));
  }, []);

  const toggleMode = useCallback(async (manual) => {
    setActionError(await writeToDb(
      () => update(ref(db, `users/${USER_ID}/brave_config`), { execution_mode: manual ? 'MANUAL' : 'AUTO' }),
      'Mode change',
    ));
  }, []);

  const isPaused = status?.paused_reason === 'DAILY_LOSS_LIMIT';
  const isManual = String(braveConfig?.execution_mode ?? 'AUTO').toUpperCase() === 'MANUAL';
  const isRunning = status?.is_running === true;

  const balance = isNum(status?.balance) ? status.balance : null;
  const equity = isNum(status?.equity) ? status.equity : null;
  const profit = isNum(status?.profit) ? status.profit : null;
  const positions = isNum(status?.open_positions) ? status.open_positions : null;
  const pnlPct = isNum(status?.session_pnl_pct) ? status.session_pnl_pct : null;
  const sessionPnl = isNum(status?.session_pnl) ? status.session_pnl : null;

  const pnlPositive = (pnlPct ?? 0) >= 0;
  const pnlBadge = pnlPct === null ? DASH : `${pnlPositive ? '↑' : '↓'} ${Math.abs(pnlPct).toFixed(1)}%`;
  const pnlToday = sessionPnl === null ? DASH : `${sessionPnl.toFixed(2)} Today`;

  const strategy = String(status?.active_strategy ?? 'Flow');
  const pairs = Array.isArray(status?.markets_analyzed) && status.markets_analyzed.length
    ? status.markets_analyzed.join(', ')
    : DASH;

  const connectionError = statusErr || configErr;

  const renderPnlBadge = () => (
    <View style={s.badgeWrap}>
      <StatusPill
        label={pnlBadge}
        tone={pnlPct === null ? 'neutral' : (pnlPositive ? 'success' : 'danger')}
      />
      <Text style={s.todayTxt}>{pnlToday}</Text>
    </View>
  );

  return (
    <View style={s.safe}>
      <ScrollView
        style={s.screen} contentContainerStyle={s.scrollDash}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.accent} />}
      >
        <Banner message={connectionError && `Connection problem — showing last known data (${connectionError})`} />
        <Banner message={actionError} />
        {status === null && !connectionError ? (
          <Banner message="Waiting for the bot to report status…" tone="warning" />
        ) : null}

        <View style={s.splitRow}>
          <View style={{ flex: 1 }}>
            <Text style={s.secLbl}>Balance</Text>
            <Text style={s.dashBigVal}>{fmt$(balance)}</Text>
            {renderPnlBadge()}
          </View>
          <View style={{ flex: 1, paddingLeft: 10 }}>
            <Text style={s.secLbl}>Equity</Text>
            <Text style={s.dashBigVal}>{fmt$(equity)}</Text>
            {renderPnlBadge()}
          </View>
        </View>

        <View style={[s.splitRow, { marginTop: theme.spacing.sm }]}>
          <View style={{ flex: 1 }}>
            <Text style={s.secLbl}>Open P & L</Text>
            <Text style={[s.dashHugeVal, {
              color: profit === null ? theme.textSecondary : (profit >= 0 ? theme.success : theme.danger),
            }]}>
              {profit === null ? DASH : `${profit >= 0 ? '+' : ''}${fmt$(profit)}`}
            </Text>
          </View>
          <View style={{ flex: 1, paddingLeft: 10 }}>
            <Text style={s.secLbl}>Positions</Text>
            <Text style={[s.dashHugeVal, { color: theme.textPrimary }]}>
              {positions === null ? DASH : String(positions)}
            </Text>
          </View>
        </View>

        <View style={s.outlineCard}>
          <View style={s.outlineRow}>
            <Text style={s.outlineLbl}>Strategy</Text>
            <Text style={s.outlineVal}>{strategy.charAt(0).toUpperCase() + strategy.slice(1)}</Text>
          </View>
          <View style={s.outlineRow}>
            <Text style={s.outlineLbl}>Bot</Text>
            <StatusPill label={isRunning ? 'Running' : 'Stopped'} tone={isRunning ? 'success' : 'neutral'} />
          </View>
          <View style={s.outlineRow}>
            <Text style={s.outlineLbl}>Trading Pairs</Text>
            <Text style={s.outlineVal}>{pairs}</Text>
          </View>
          <View style={s.outlineRow}>
            <Text style={s.outlineLbl}>Last Updated</Text>
            <Text style={s.outlineVal}>{fmtTime(status?.last_updated)}</Text>
          </View>
        </View>

        <View style={s.btnRow}>
          <TouchableOpacity
            style={[s.halfBtn, { marginRight: 12, backgroundColor: !isManual ? TINT.accent : theme.surface }]}
            onPress={() => toggleMode(false)}
          >
            <Text style={[s.halfBtnTxt, { color: !isManual ? theme.accent : theme.textMuted }]}>AUTO</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[s.halfBtn, { backgroundColor: isManual ? theme.surfaceRaised : theme.surface }]}
            onPress={() => toggleMode(true)}
          >
            <Text style={[s.halfBtnTxt, { color: isManual ? theme.textPrimary : theme.textMuted }]}>MANUAL</Text>
          </TouchableOpacity>
        </View>

        {isPaused ? <Banner message="Daily loss limit hit — bot paused for today" /> : null}

        <View style={s.btnRow}>
          <TouchableOpacity style={[s.actionStop, { flex: 1 }]} onPress={() => sendCommand('stop')}>
            <Text style={s.actionTxt}>STOP</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[s.actionStart, { flex: 1, marginLeft: 16 }]} onPress={() => sendCommand('start')}>
            <Text style={s.actionTxt}>START</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </View>
  );
});

// ════════════════════════════════════════════════════════════════════
// SIGNALS SCREEN
// ════════════════════════════════════════════════════════════════════
const SignalsScreen = memo(function SignalsScreen() {
  const [signals, signalsErr] = useFirebaseValue(`users/${USER_ID}/pending_signals`, {});
  const [expandedIds, setExpandedIds] = useState({});
  const [busyKey, setBusyKey] = useState(null);
  const [actionError, setActionError] = useState(null);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);

  const respond = useCallback(async (key, response) => {
    setBusyKey(key);
    const error = await writeToDb(
      () => update(ref(db, `users/${USER_ID}/pending_signals/${key}`), {
        status: response,
        responded_at: new Date().toISOString(),
      }),
      response === 'CONFIRMED' ? 'Accepting signal' : 'Rejecting signal',
    );
    if (!mounted.current) return;
    setActionError(error);
    setBusyKey(null);
  }, []);

  const toggleExpand = useCallback((key) => {
    setExpandedIds(prev => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const data = useMemo(() => {
    const all = Object.entries(signals ?? {}).filter(([, v]) => v && typeof v === 'object');
    const sort = byNewest(v => v.pushed_at);
    const pending = all.filter(([, v]) => String(v.status ?? '').toUpperCase() === 'PENDING')
      .sort(([, a], [, b]) => sort(a, b));
    const history = all.filter(([, v]) => String(v.status ?? '').toUpperCase() !== 'PENDING')
      .sort(([, a], [, b]) => sort(a, b))
      .slice(0, 15);
    return [...pending, ...history];
  }, [signals]);

  return (
    <View style={s.safe}>
      <View style={s.screen}>
        <View style={{ paddingHorizontal: theme.spacing.md, paddingTop: 12 }}>
          <Banner message={signalsErr && `Connection problem — ${signalsErr}`} />
          <Banner message={actionError} />
        </View>
        {data.length === 0 ? (
          <EmptyState
            icon="bell-outline"
            title="No pending signals"
            detail="Confirmed and expired signals will appear here."
          />
        ) : (
          <ScrollView
            contentContainerStyle={[s.scrollList, { paddingBottom: 100 }]}
            showsVerticalScrollIndicator={false}
          >
            {data.map(([key, sig]) => (
              <SignalCard
                key={key}
                sigKey={key}
                sig={sig}
                isExpanded={!!expandedIds[key]}
                isBusy={busyKey === key}
                onToggle={toggleExpand}
                onRespond={respond}
              />
            ))}
          </ScrollView>
        )}
      </View>
    </View>
  );
});

// ════════════════════════════════════════════════════════════════════
// INSIGHTS SCREEN — news only (DeepSeek verdict over Finnhub headlines)
// ════════════════════════════════════════════════════════════════════
const InsightsScreen = memo(function InsightsScreen() {
  const [news, newsErr] = useFirebaseValue(`users/${USER_ID}/news_analysis`, {});

  const entries = useMemo(() => {
    const sort = byNewest(v => v.updated_at);
    return Object.entries(news ?? {})
      .filter(([, d]) => d && typeof d === 'object')
      .sort(([, a], [, b]) => sort(a, b));
  }, [news]);

  return (
    <View style={s.safe}>
      <ScrollView style={s.screen} contentContainerStyle={[s.scrollList, { paddingBottom: 100 }]}>
        <Banner message={newsErr && `Connection problem — ${newsErr}`} />

        {entries.length === 0 && !newsErr ? (
          <EmptyState
            icon="newspaper-variant-outline"
            title="No news analysis yet"
            detail="DeepSeek analyses Finnhub headlines whenever Flow detects a signal."
          />
        ) : null}

        {entries.map(([pair, data]) => {
          const verdict = String(data.verdict ?? 'UNCERTAIN').toUpperCase();
          const vTone = verdict === 'CONFIRM' ? 'success' : verdict === 'OPPOSE' ? 'danger' : 'accent';
          const headlines = Array.isArray(data.headlines) ? data.headlines : [];
          const count = isNum(data.article_count) ? data.article_count : headlines.length;

          return (
            <View key={pair} style={s.insightGroup}>
              <View style={s.sigCardInner}>
                <View style={s.sigLeft}>
                  <PairIcon symbol={data.symbol || pair} size={42} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={s.sigTitle}>{data.symbol || pair}</Text>
                  <Text style={s.sigSub}>News check on {data.direction || DASH} signal</Text>
                </View>
                <View style={{ alignItems: 'flex-end' }}>
                  <StatusPill label={verdict} tone={vTone} />
                  <Text style={s.sigSlTp}>{fmtTime(data.updated_at)}</Text>
                </View>
              </View>

              <Text style={[s.gptText, { color: theme.textPrimary, marginTop: theme.spacing.md, marginBottom: 0 }]}>
                {data.reason || 'No reason provided.'}
              </Text>

              <View style={s.gptBox}>
                <Text style={s.gptTitle}>Finnhub headlines ({count})</Text>
                {headlines.length === 0 ? (
                  <Text style={s.gptText}>No relevant headlines found for this symbol.</Text>
                ) : (
                  headlines.map((h, j) => (
                    <Text key={j} style={s.gptText} numberOfLines={3}>• {h?.headline || DASH}</Text>
                  ))
                )}
                <Text style={s.gptFoot}>
                  Source: {data.news_source || 'Finnhub'} · Model: {data.model || 'deepseek-chat'}
                </Text>
              </View>
            </View>
          );
        })}
      </ScrollView>
    </View>
  );
});

// ════════════════════════════════════════════════════════════════════
// ALERTS SCREEN
// ════════════════════════════════════════════════════════════════════
const AlertsScreen = memo(function AlertsScreen() {
  const [alerts, alertsErr] = useFirebaseValue(`users/${USER_ID}/alerts`, {});

  const data = useMemo(() => {
    const sort = byNewest(v => v.sent_at);
    return Object.entries(alerts ?? {})
      .filter(([, v]) => v && typeof v === 'object')
      .sort(([, a], [, b]) => sort(a, b))
      .slice(0, 30);
  }, [alerts]);

  return (
    <View style={s.safe}>
      <View style={s.screen}>
        <View style={{ paddingHorizontal: theme.spacing.md, paddingTop: 12 }}>
          <Banner message={alertsErr && `Connection problem — ${alertsErr}`} />
        </View>
        {data.length === 0 ? (
          <EmptyState
            icon="alert-outline"
            title="No alerts yet"
            detail="Executed trades and failures show up here."
          />
        ) : (
          <ScrollView
            contentContainerStyle={[s.scrollList, { paddingBottom: 100 }]}
            showsVerticalScrollIndicator={false}
          >
            {data.map(([key, sig]) => {
              const isBuy = sig.direction === 'BUY';
              const type = String(sig.alert_type ?? '').toUpperCase();
              const isExecuted = type === 'TRADE_EXECUTED';
              const isFailed = type === 'EXECUTION_FAILED';
              const pairName = getPairName(sig.symbol);
              const strategyName = sig.strategy || null;
              const subText = isExecuted
                ? 'Trade executed'
                : isFailed
                  ? 'Execution failed'
                  : pairName;

              return (
                <View key={key} style={s.sigCard}>
                  <View style={s.sigLeft}>
                    <PairIcon symbol={sig.symbol} size={42} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={s.sigTitle}>{sig.symbol ?? DASH}</Text>
                    {subText ? <Text style={s.sigSub}>{subText}</Text> : null}
                    {isExecuted ? (
                      <Text style={[s.sigSlTp, { marginTop: theme.spacing.xs }]}>
                        Ticket #{sig.ticket ?? DASH} · {sig.lot ?? DASH} lots @ {sig.filled_price ?? DASH}
                      </Text>
                    ) : null}
                    {isFailed && sig.error ? (
                      <Text style={[s.sigSlTp, { color: theme.danger, marginTop: theme.spacing.xs }]} numberOfLines={2}>
                        {sig.error}
                      </Text>
                    ) : null}
                  </View>
                  <View style={{ alignItems: 'flex-end' }}>
                    <StatusPill label={sig.direction ?? DASH} tone={isBuy ? 'success' : 'danger'} />
                    {isExecuted || isFailed ? (
                      strategyName ? <Text style={s.sigSlTp}>{strategyName}</Text> : null
                    ) : (
                      <Text style={s.sigSlTp}>
                        SL: {sig.suggested_sl ?? DASH}, TP: {sig.suggested_tp ?? DASH}
                      </Text>
                    )}
                    <Text style={s.sigSlTp}>{fmtTime(sig.sent_at)}</Text>
                  </View>
                </View>
              );
            })}
          </ScrollView>
        )}
      </View>
    </View>
  );
});

// ════════════════════════════════════════════════════════════════════
// BROKER ACCOUNT CARD — edits users/<id>/mt5_config
// ════════════════════════════════════════════════════════════════════
const PASSWORD_MASK = '••••••••';

const BrokerAccountCard = memo(function BrokerAccountCard() {
  const [mt5Config, configErr] = useFirebaseValue(`users/${USER_ID}/mt5_config`);

  const [login, setLogin] = useState('');
  const [server, setServer] = useState('');
  const [password, setPassword] = useState('');
  const [editingPassword, setEditingPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  const touched = useRef(false);
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const hasStoredPassword = typeof mt5Config?.password === 'string' && mt5Config.password.length > 0;

  useEffect(() => {
    if (touched.current || !mt5Config) return;
    setLogin(mt5Config.login === undefined || mt5Config.login === null ? '' : String(mt5Config.login));
    setServer(String(mt5Config.server ?? ''));
  }, [mt5Config]);

  const edit = (setter) => (value) => {
    touched.current = true;
    setSaved(false);
    setError(null);
    setter(value);
  };

  const startPasswordEdit = useCallback(() => {
    touched.current = true;
    setSaved(false);
    setError(null);
    setPassword('');
    setEditingPassword(true);
  }, []);

  const cancelPasswordEdit = useCallback(() => {
    setPassword('');
    setEditingPassword(false);
  }, []);

  const save = useCallback(async () => {
    const loginTrimmed = login.trim();
    const serverTrimmed = server.trim();

    if (!/^\d+$/.test(loginTrimmed) || Number(loginTrimmed) <= 0) {
      setError('Login must be a positive account number.');
      return;
    }
    if (!serverTrimmed) {
      setError('Server is required (e.g. RoboForex-Pro).');
      return;
    }
    if (!hasStoredPassword && !password.trim()) {
      setError('Password is required.');
      return;
    }

    const payload = {
      login: Number(loginTrimmed),
      server: serverTrimmed,
      updated_at: new Date().toISOString(),
      updated_by: 'mobile',
    };
    if (editingPassword && password.trim()) {
      payload.password = password;
    }

    setSaving(true);
    const err = await writeToDb(
      () => update(ref(db, `users/${USER_ID}/mt5_config`), payload),
      'Saving broker credentials',
    );
    if (!mounted.current) return;

    setSaving(false);
    setError(err);
    if (!err) {
      setPassword('');
      setEditingPassword(false);
      touched.current = false;
      setSaved(true);
    }
  }, [login, server, password, editingPassword, hasStoredPassword]);

  return (
    <View style={s.setCard}>
      <View style={s.setRow}>
        <Text style={s.setLabel}>Broker Account</Text>
      </View>

      <View style={s.fieldWrap}>
        <Banner message={configErr && `Could not load saved credentials — ${configErr}`} />
        <Banner message={error} />
        {saved ? (
          <View style={s.successBand}>
            <Text style={s.successTxt}>
              Broker credentials updated — will apply on next bot restart
            </Text>
          </View>
        ) : null}

        <Text style={s.fieldLabel}>Login</Text>
        <TextInput
          style={s.input}
          value={login}
          onChangeText={edit(setLogin)}
          keyboardType="number-pad"
          placeholder="68343238"
          placeholderTextColor={theme.textMuted}
          autoCorrect={false}
        />

        <Text style={s.fieldLabel}>Password</Text>
        {!editingPassword && hasStoredPassword ? (
          <View style={s.inputRow}>
            <View style={[s.input, s.inputDisabled, { flex: 1 }]}>
              <Text style={s.inputMasked}>{PASSWORD_MASK}</Text>
            </View>
            <TouchableOpacity style={s.inlineBtn} onPress={startPasswordEdit}>
              <Text style={s.inlineBtnTxt}>EDIT</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <View style={s.inputRow}>
            <TextInput
              style={[s.input, { flex: 1 }]}
              value={password}
              onChangeText={edit(setPassword)}
              secureTextEntry
              placeholder={hasStoredPassword ? 'Enter new password' : 'Account password'}
              placeholderTextColor={theme.textMuted}
              autoCapitalize="none"
              autoCorrect={false}
            />
            {hasStoredPassword ? (
              <TouchableOpacity style={s.inlineBtn} onPress={cancelPasswordEdit}>
                <Text style={s.inlineBtnTxt}>CANCEL</Text>
              </TouchableOpacity>
            ) : null}
          </View>
        )}
        {!editingPassword && hasStoredPassword ? (
          <Text style={s.fieldHint}>Saved password is hidden. Tap EDIT to replace it.</Text>
        ) : null}

        <Text style={s.fieldLabel}>Server</Text>
        <TextInput
          style={s.input}
          value={server}
          onChangeText={edit(setServer)}
          placeholder="RoboForex-Pro"
          placeholderTextColor={theme.textMuted}
          autoCapitalize="none"
          autoCorrect={false}
        />

        <TouchableOpacity
          style={[s.saveBtn, saving && { opacity: 0.6 }]}
          onPress={save}
          disabled={saving}
        >
          {saving
            ? <ActivityIndicator color={theme.textPrimary} />
            : <Text style={s.saveBtnTxt}>SAVE</Text>}
        </TouchableOpacity>

        <Text style={s.fieldNote}>
          The bot reads broker credentials only when it starts. Saving here will not move a
          running bot to a different account — restart the bot to apply changes.
        </Text>
      </View>
    </View>
  );
});

// ════════════════════════════════════════════════════════════════════
// SETTINGS SCREEN — Flow is the only strategy
// ════════════════════════════════════════════════════════════════════
const SettingsScreen = memo(function SettingsScreen() {
  const [braveConfig, configErr] = useFirebaseValue(`users/${USER_ID}/brave_config`);
  const [health, healthErr] = useFirebaseValue(`users/${USER_ID}/health`);
  const [expanded, setExpanded] = useState(false);
  const [actionError, setActionError] = useState(null);

  const flowCfg = useMemo(() => {
    const cfg = braveConfig?.strategy_config?.flow;
    return cfg && typeof cfg === 'object' ? cfg : { enabled: true, max_trades: 2 };
  }, [braveConfig]);

  const enabled = flowCfg.enabled !== false;

  const toggleFlow = useCallback(async (next) => {
    setActionError(await writeToDb(
      () => update(ref(db, `users/${USER_ID}/brave_config`), {
        'strategy_config/flow/enabled': next,
        active_strategy: 'flow',
      }),
      next ? 'Enabling Flow' : 'Disabling Flow',
    ));
  }, []);

  const healthStatus = health?.status ?? DASH;

  return (
    <View style={s.safe}>
      <ScrollView style={s.screen} contentContainerStyle={[s.scrollList, { paddingBottom: 100 }]}>
        <Banner message={(configErr || healthErr) && `Connection problem — ${configErr || healthErr}`} />
        <Banner message={actionError} />

        <BrokerAccountCard />

        <View style={s.setCard}>
          <TouchableOpacity
            style={[s.setRow, enabled && s.raisedRow]}
            onPress={() => setExpanded(e => !e)}
          >
            <Text style={s.setLabel}>Flow</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
              <StatusPill label={enabled ? 'Active' : 'Off'} tone={enabled ? 'success' : 'neutral'} />
              <MaterialCommunityIcons
                name={expanded ? 'chevron-down' : 'chevron-right'} size={24} color={theme.textSecondary}
              />
            </View>
          </TouchableOpacity>

          {expanded ? (
            <View>
              <View style={s.setRow}>
                <Text style={s.setKey}>News</Text>
                <Text style={s.setValStr}>DeepSeek + Finnhub</Text>
              </View>
              <View style={s.setRow}>
                <Text style={s.setKey}>Max trades per symbol</Text>
                <Text style={s.setValStr}>{String(flowCfg.max_trades ?? 2)}</Text>
              </View>
            </View>
          ) : null}

          <View style={s.setRow}>
            <Text style={s.setKey}>Enabled</Text>
            <TouchableOpacity
              style={[s.toggleOff, enabled && { backgroundColor: theme.success }]}
              onPress={() => toggleFlow(!enabled)}
            >
              <View style={[s.toggleThumbOff, enabled && { alignSelf: 'flex-end' }]} />
            </TouchableOpacity>
          </View>
        </View>

        <View style={s.setCard}>
          <View style={s.setRow}>
            <Text style={s.setKeyB}>Status</Text>
            <StatusPill
              label={healthStatus}
              tone={healthStatus === 'HEALTHY' ? 'success' : healthStatus === DASH ? 'neutral' : 'accent'}
            />
          </View>
          <View style={s.setRow}>
            <Text style={s.setKeyB}>Execution Mode</Text>
            <Text style={s.setValMuted}>
              {String(braveConfig?.execution_mode ?? health?.execution_mode ?? DASH).toUpperCase()}
            </Text>
          </View>
          <View style={s.setRow}>
            <Text style={s.setKeyB}>MT5 Connected</Text>
            <Text style={s.setValMuted}>{health?.mt5_connected === true ? 'Yes' : 'No'}</Text>
          </View>
          <View style={s.setRow}>
            <Text style={s.setKeyB}>Account Trading</Text>
            <Text style={s.setValMuted}>
              {health?.account_trade_allowed === true ? 'Allowed' : 'Restricted'}
            </Text>
          </View>
          <View style={s.setRow}>
            <Text style={s.setKeyB}>Bot Version</Text>
            <Text style={s.setValMuted}>{health?.bot_version ?? DASH}</Text>
          </View>
          <View style={s.setRow}>
            <Text style={s.setKeyB}>Updated</Text>
            <Text style={s.setValMuted}>{fmtTime(health?.timestamp)}</Text>
          </View>
        </View>
      </ScrollView>
    </View>
  );
});

// ════════════════════════════════════════════════════════════════════
// ERROR BOUNDARY — a render error must not white-screen the whole app
// ════════════════════════════════════════════════════════════════════
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('[Brave] Render error:', error?.message, info?.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <View style={[s.safe, { justifyContent: 'center', padding: theme.spacing.xl }]}>
        <Text style={s.emptyTitle}>Something went wrong</Text>
        <Text style={s.emptyDetail}>{String(this.state.error?.message ?? this.state.error)}</Text>
        <TouchableOpacity
          style={[s.halfBtn, { backgroundColor: theme.accent, marginTop: theme.spacing.lg }]}
          onPress={() => this.setState({ error: null })}
        >
          <Text style={[s.halfBtnTxt, { color: theme.textPrimary }]}>TRY AGAIN</Text>
        </TouchableOpacity>
      </View>
    );
  }
}

// ════════════════════════════════════════════════════════════════════
// TAB NAVIGATOR
// ════════════════════════════════════════════════════════════════════
const TAB_ICONS = {
  Dashboard: 'poll',
  Signals: 'fire',
  Insights: 'chart-timeline-variant',
  Alerts: 'alert-outline',
  Settings: 'cog-outline',
};

export default function App() {
  return (
    <SafeAreaProvider>
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.bg }} edges={['top', 'left', 'right']}>
        <StatusBar style="light" backgroundColor={theme.bg} translucent={false} />
        <ErrorBoundary>
          <NavigationContainer>
            <Tab.Navigator
              screenOptions={({ route }) => ({
                headerShown: false,
                tabBarShowLabel: false,
                tabBarStyle: {
                  position: 'absolute',
                  backgroundColor: theme.surface,
                  bottom: 24,
                  left: 20,
                  right: 20,
                  height: 72,
                  borderRadius: theme.radius.lg,
                  borderTopWidth: 0,
                  paddingBottom: 0,
                },
                tabBarActiveTintColor: theme.textPrimary,
                tabBarInactiveTintColor: theme.textMuted,
                tabBarIcon: ({ focused }) => (
                  <View style={[s.tabPill, focused && s.tabPillActive]}>
                    <MaterialCommunityIcons
                      name={TAB_ICONS[route.name] || 'help'}
                      size={22}
                      color={focused ? theme.textPrimary : theme.textMuted}
                    />
                    <Text style={[s.tabLbl, { color: focused ? theme.textPrimary : theme.textMuted }]}>
                      {route.name}
                    </Text>
                  </View>
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
        </ErrorBoundary>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

// ════════════════════════════════════════════════════════════════════
// STYLES
// ════════════════════════════════════════════════════════════════════
const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.bg },
  screen: { flex: 1, backgroundColor: theme.bg },
  scrollDash: { paddingHorizontal: theme.spacing.md, paddingTop: theme.spacing.lg, paddingBottom: 120 },
  scrollList: { paddingHorizontal: theme.spacing.md, paddingTop: 20, paddingBottom: 120 },

  splitRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: theme.spacing.lg },
  secLbl: {
    fontSize: 12, fontWeight: '500', color: theme.textMuted,
    textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: theme.spacing.sm,
  },
  dashBigVal: { fontSize: 36, fontWeight: '700', color: theme.textPrimary, marginBottom: 12 },
  dashHugeVal: { fontSize: 44, fontWeight: '700', marginBottom: theme.spacing.xs },
  badgeWrap: { flexDirection: 'row', alignItems: 'center', gap: theme.spacing.sm },
  todayTxt: { fontSize: 13, color: theme.textSecondary },

  pill: {
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
    alignSelf: 'flex-start',
  },
  pillTxt: { fontSize: 11, fontWeight: '700' },

  outlineCard: {
    backgroundColor: theme.surface,
    borderRadius: theme.radius.md,
    padding: theme.spacing.md,
    marginBottom: theme.spacing.md,
  },
  outlineRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 10 },
  outlineLbl: { color: theme.textSecondary, fontSize: 14, fontWeight: '400' },
  outlineVal: { color: theme.textPrimary, fontSize: 14, fontWeight: '600', flexShrink: 1, textAlign: 'right' },

  btnRow: { flexDirection: 'row', marginBottom: theme.spacing.md },
  halfBtn: { flex: 1, height: 50, borderRadius: theme.radius.sm, justifyContent: 'center', alignItems: 'center' },
  halfBtnTxt: { fontSize: 15, fontWeight: '700', letterSpacing: 0.5 },

  actionStop: {
    backgroundColor: theme.danger, borderRadius: theme.radius.md, height: 56,
    justifyContent: 'center', alignItems: 'center',
  },
  actionStart: {
    backgroundColor: theme.success, borderRadius: theme.radius.md, height: 56,
    justifyContent: 'center', alignItems: 'center',
  },
  actionTxt: { color: theme.textPrimary, fontSize: 16, fontWeight: '700', letterSpacing: 0.5 },

  sigCard: {
    flexDirection: 'row', backgroundColor: theme.surface,
    borderRadius: theme.radius.md, padding: theme.spacing.md,
    marginBottom: theme.spacing.md, alignItems: 'center',
  },
  sigCardCol: {
    backgroundColor: theme.surface,
    borderRadius: theme.radius.md, padding: theme.spacing.md,
    marginBottom: theme.spacing.md,
  },
  sigCardTop: { flexDirection: 'row', alignItems: 'center' },
  sigCardInner: { flexDirection: 'row', alignItems: 'center' },
  sigExpanded: { marginTop: theme.spacing.md, paddingTop: theme.spacing.md },
  sigBtnRow: { flexDirection: 'row', justifyContent: 'space-between', gap: 12 },
  sigAcceptBtn: {
    flex: 1, backgroundColor: theme.success, paddingVertical: 12,
    borderRadius: theme.radius.sm, alignItems: 'center',
  },
  sigRejectBtn: {
    flex: 1, backgroundColor: theme.surfaceRaised, paddingVertical: 12,
    borderRadius: theme.radius.sm, alignItems: 'center',
  },
  sigBtnTxt: { color: theme.textPrimary, fontWeight: '700', fontSize: 14 },

  sigLeft: { marginRight: 14 },
  sigTitle: { color: theme.textPrimary, fontSize: 17, fontWeight: '600', marginBottom: 2 },
  sigSub: { color: theme.textSecondary, fontSize: 14, fontWeight: '400' },
  sigSlTp: { color: theme.textSecondary, fontSize: 12, marginTop: theme.spacing.xs },

  setCard: {
    backgroundColor: theme.surface,
    borderRadius: theme.radius.md,
    padding: theme.spacing.md,
    marginBottom: theme.spacing.md,
  },
  setRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingVertical: 10,
  },
  raisedRow: {
    backgroundColor: theme.surfaceRaised,
    borderRadius: theme.radius.sm,
    paddingHorizontal: theme.spacing.sm,
  },
  setLabel: { color: theme.textPrimary, fontSize: 17, fontWeight: '600' },
  setKey: { color: theme.textSecondary, fontSize: 14, fontWeight: '400' },
  setValStr: { color: theme.textPrimary, fontSize: 14, fontWeight: '600' },
  setKeyB: { color: theme.textSecondary, fontSize: 14, fontWeight: '400' },
  setValMuted: { color: theme.textSecondary, fontSize: 14, fontWeight: '400' },

  fieldWrap: { paddingTop: theme.spacing.xs },
  fieldLabel: {
    fontSize: 12, fontWeight: '500', color: theme.textMuted,
    textTransform: 'uppercase', letterSpacing: 0.5,
    marginBottom: theme.spacing.xs, marginTop: 14,
  },
  fieldHint: { color: theme.textSecondary, fontSize: 12, marginTop: 6 },
  fieldNote: { color: theme.textSecondary, fontSize: 12, lineHeight: 18, marginTop: theme.spacing.md },
  input: {
    backgroundColor: theme.surfaceRaised, borderRadius: theme.radius.sm,
    paddingHorizontal: 14, paddingVertical: 12, color: theme.textPrimary, fontSize: 15,
    minHeight: 46, justifyContent: 'center',
  },
  inputDisabled: { backgroundColor: theme.bg },
  inputMasked: { color: theme.textSecondary, fontSize: 15, letterSpacing: 2 },
  inputRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  inlineBtn: {
    paddingHorizontal: 14, paddingVertical: 12, borderRadius: theme.radius.sm,
    backgroundColor: theme.surfaceRaised, minHeight: 46, justifyContent: 'center',
  },
  inlineBtnTxt: { color: theme.textPrimary, fontSize: 12, fontWeight: '700', letterSpacing: 0.5 },
  saveBtn: {
    marginTop: 22, height: 50, borderRadius: theme.radius.sm, backgroundColor: theme.accent,
    justifyContent: 'center', alignItems: 'center',
  },
  saveBtnTxt: { color: theme.textPrimary, fontSize: 15, fontWeight: '700', letterSpacing: 0.5 },
  successBand: {
    backgroundColor: TINT.success, padding: 10,
    marginBottom: 12, borderRadius: theme.radius.sm,
  },
  successTxt: { fontSize: 12, color: theme.success },

  toggleOff: {
    width: 44, height: 26, borderRadius: 13, backgroundColor: theme.surfaceRaised,
    justifyContent: 'center', paddingHorizontal: 3,
  },
  toggleThumbOff: { width: 20, height: 20, borderRadius: 10, backgroundColor: theme.textPrimary },

  insightGroup: {
    backgroundColor: theme.surface, borderRadius: theme.radius.md,
    padding: theme.spacing.md, marginBottom: theme.spacing.md,
  },
  gptBox: {
    marginTop: theme.spacing.md, borderRadius: theme.radius.sm,
    padding: theme.spacing.md, backgroundColor: theme.surfaceRaised,
  },
  gptTitle: { color: theme.textPrimary, fontSize: 14, fontWeight: '600', marginBottom: 12 },
  gptText: { color: theme.textSecondary, fontSize: 14, lineHeight: 22, marginBottom: theme.spacing.sm },
  gptFoot: { color: theme.textMuted, fontSize: 12, marginTop: 6 },

  warnBand: {
    padding: 10, marginBottom: theme.spacing.md, borderRadius: theme.radius.sm,
  },
  warnBandTxt: { fontSize: 12 },
  emptyWrap: {
    flex: 1, justifyContent: 'center', alignItems: 'center',
    paddingHorizontal: 40, gap: theme.spacing.sm,
  },
  emptyTitle: { color: theme.textPrimary, fontSize: 17, fontWeight: '600', textAlign: 'center' },
  emptyDetail: { color: theme.textMuted, fontSize: 14, lineHeight: 20, textAlign: 'center' },

  tabPill: {
    alignItems: 'center', justifyContent: 'center',
    borderRadius: theme.radius.sm,
    paddingHorizontal: 10, paddingVertical: 6, minWidth: 58,
  },
  tabPillActive: { backgroundColor: theme.surfaceRaised },
  tabLbl: { fontSize: 10, fontWeight: '600', marginTop: 2 },
});
