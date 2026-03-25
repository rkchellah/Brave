import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import React, { useState, useEffect, useCallback, useMemo, memo } from 'react';
import {
  StyleSheet, Text, View, ScrollView, TouchableOpacity,
  RefreshControl, Dimensions, Appearance,
  Platform
} from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { initializeApp, getApps } from 'firebase/app';
import { getDatabase, ref, onValue, set, update } from 'firebase/database';
import { FlashList } from '@shopify/flash-list';
import { StatusBar } from 'expo-status-bar';
import * as SplashScreen from 'expo-splash-screen';

// Prevent splash screen from hiding automatically
SplashScreen.preventAutoHideAsync();

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
const C = {
  bg: '#10141D',          // Main background
  card: '#1A1F2B',        // Card background
  border: '#2A303D',      // Card borders / dividers
  primary: '#FFFFFF',     // Main text
  secondary: '#8F96A6',   // Muted text
  success: '#20C997',     // Teal green
  danger: '#FF5C6C',      // Pinkish red
  warning: '#FFAD00',     // Yellow for AUTO
  manualBtn: '#6C7486',   // Grey for MANUAL
  tabBg: '#191E2B',       // Floating tab bar bg
  tabActive: '#FFFFFF',
  tabInactive: '#6D7484',
};

// ── Helpers ────────────────────────────────────────────────────────
const fmt$ = (n) => `$${(n ?? 0).toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtTime = (iso) => iso ? new Date(iso).toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit' }) : '—';

// ── Pair icon colours ──────────────────────────────────────────────
const getFlags = (sym) => {
  const s = String(sym).toUpperCase();
  if (s.includes('USDJPY')) return ['🇺🇸', '🇯🇵'];
  if (s.includes('EURUSD')) return ['🇪🇺', '🇺🇸'];
  if (s.includes('GBPUSD')) return ['🇬🇧', '🇺🇸'];
  if (s.includes('AUDUSD')) return ['🇦🇺', '🇺🇸'];
  if (s.includes('IDR/USD')) return ['🇮🇩', '🇺🇸'];
  return null;
};

// Extracted mock subtitle for the concepts to match screenshots exactly
const getMockPairNames = (sym) => {
  const s = String(sym).toUpperCase();
  if (s.includes('USDJPY')) return 'Euro / U.S. Dollar';
  if (s.includes('AUDUSD')) return 'Euro / U.S. Dollar';
  if (s.includes('IDR/USD')) return 'Rupiah / U.S. Dollar';
  if (s === 'TESLA') return 'Tesal, Inc.';
  if (s === 'DHDI') return 'PT. Duatiga Pertama';
  if (s === 'AMRI') return 'PT. Atma Merapi';
  if (s === 'BOE') return 'Boeing Co';
  return null;
}

const PairIcon = memo(function PairIcon({ symbol, size = 44 }) {
  const s = String(symbol).toUpperCase();
  const flags = getFlags(s);
  
  if (flags) {
    return (
      <View style={{ width: size, height: size }}>
        {/* Top Right Flag */}
        <View style={{
          position: 'absolute', top: 0, right: 0,
          width: size * 0.65, height: size * 0.65,
          borderRadius: size, backgroundColor: '#1A1E29',
          justifyContent: 'center', alignItems: 'center', zIndex: 1,
          borderColor: '#111', borderWidth: 1, overflow: 'hidden'
        }}>
          <Text style={{ fontSize: size * 0.4, marginTop: -2 }}>{flags[1]}</Text>
        </View>
        {/* Bottom Left Flag */}
        <View style={{
          position: 'absolute', bottom: 0, left: 0,
          width: size * 0.65, height: size * 0.65,
          borderRadius: size, backgroundColor: '#1A1E29',
          justifyContent: 'center', alignItems: 'center', zIndex: 2,
          borderColor: '#111', borderWidth: 1, overflow: 'hidden'
        }}>
          <Text style={{ fontSize: size * 0.4, marginTop: -2 }}>{flags[0]}</Text>
        </View>
      </View>
    );
  }

  let bg = '#333';
  let initial = s ? s.charAt(0) : '?';
  let icon = null;

  if (s === 'TESLA') { bg = '#E31937'; icon = <MaterialCommunityIcons name="alpha-t" size={size * 0.6} color="#FFF" />; }
  else if (s === 'DHDI') { bg = '#F2A900'; icon = <MaterialCommunityIcons name="currency-chf" size={size * 0.5} color="#FFF" />; }
  else if (s === 'AMRI') { bg = '#E84142'; icon = <MaterialCommunityIcons name="triangle-outline" size={size * 0.5} color="#FFF" />; }
  else if (s === 'BOE') { bg = '#0033A0'; icon = <MaterialCommunityIcons name="run-fast" size={size * 0.5} color="#FFF" style={{transform:[{rotate:'-45deg'}]}} />; }

  return (
    <View style={{
      width: size, height: size, borderRadius: size / 2,
      backgroundColor: bg, alignItems: 'center', justifyContent: 'center',
      borderWidth: 1, borderColor: '#111'
    }}>
      {icon ? icon : <Text style={{ color: '#FFF', fontSize: size * 0.4, fontWeight: '700' }}>{initial}</Text>}
    </View>
  );
});

const SignalTimer = memo(function SignalTimer({ expiresAt }) {
  const [now, setNow] = useState(Date.now());
  
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, []);

  const secs = Math.max(0, Math.floor(expiresAt - now / 1000));
  const timeStr = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
  
  return (
    <Text style={[s.sigSub, { color: C.warning, marginBottom: 16, textAlign: 'center' }]}>
      Expires in {timeStr}
    </Text>
  );
});

const SignalCard = memo(function SignalCard({ sigKey, sig, isExpanded, onToggle, onRespond }) {
  const isBuy = sig.direction === 'BUY';
  const subText = getMockPairNames(sig.symbol) || sig.strategy_name || 'Stock / Pair Info';
  const isHistory = sig.status !== 'PENDING';

  return (
    <View style={s.sigCardCol}>
      <TouchableOpacity 
        style={s.sigCardTop} 
        onPress={() => onToggle?.(sigKey)} 
        activeOpacity={0.7}
        disabled={isHistory}
      >
        <View style={s.sigLeft}>
          <PairIcon symbol={sig.symbol} size={42} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={s.sigTitle}>{sig.symbol}</Text>
          <Text style={s.sigSub}>{subText}</Text>
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <Text style={[s.sigDir, { color: isBuy ? C.success : C.danger }]}>{sig.direction}</Text>
          <Text style={s.sigSlTp}>Entry: {sig.entry_price ?? '—'}</Text>
          <Text style={s.sigSlTp}>SL: {sig.suggested_sl}, TP: {sig.suggested_tp}</Text>
        </View>
      </TouchableOpacity>

      {isExpanded && !isHistory && (
        <View style={s.sigExpanded}>
          <SignalTimer expiresAt={sig.expires_at} />
          <View style={s.sigBtnRow}>
            <TouchableOpacity onPress={() => onRespond(sigKey, 'CONFIRMED')} style={s.sigAcceptBtn}>
              <Text style={s.sigAcceptBtnTxt}>ACCEPT</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => onRespond(sigKey, 'REJECTED')} style={s.sigRejectBtn}>
              <Text style={s.sigRejectBtnTxt}>REJECT</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}
    </View>
  );
});

// ════════════════════════════════════════════════════════════════════
// DASHBOARD SCREEN
// ════════════════════════════════════════════════════════════════════
const DashboardScreen = memo(function DashboardScreen() {
  const [status, setStatus] = useState(null);
  const [braveConfig, setBraveConfig] = useState(null);
  const [sentiment, setSentiment] = useState({});
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    const u1 = onValue(ref(db, `users/${USER_ID}/bot_status`), s => setStatus(s.val()));
    const u2 = onValue(ref(db, `users/${USER_ID}/brave_config`), s => setBraveConfig(s.val()));
    const u3 = onValue(ref(db, `users/${USER_ID}/sentiment`), s => setSentiment(s.val() ?? {}));
    return () => { u1(); u2(); u3(); };
  }, []);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    setTimeout(() => setRefreshing(false), 800);
  }, []);

  const sendCommand = useCallback(async (action) => {
    await set(ref(db, `users/${USER_ID}/commands`), { action, timestamp: new Date().toISOString() });
  }, []);

  const toggleMode = useCallback(async (manual) => {
    await update(ref(db, `users/${USER_ID}/brave_config`), { execution_mode: manual ? 'MANUAL' : 'AUTO' });
  }, []);

  const isPaused = status?.paused_reason === 'DAILY_LOSS_LIMIT';
  const isManual = String(braveConfig?.execution_mode ?? 'AUTO').toUpperCase() === 'MANUAL';
  const balance = status?.balance ?? 350.61;
  const equity = status?.equity ?? 350.61;
  const profit = status?.profit ?? -10.40;
  const positions = status?.open_positions ?? 1;
  const pnlPct = status?.session_pnl_pct ?? -20.6;
  const pnlPctDisp = Math.abs(pnlPct).toFixed(1);
  const pnlPctArrow = pnlPct >= 0 ? '↑' : '↓';
  const pnlTodayStr = (status?.session_pnl ?? -9.00).toFixed(2);
  const badgeColor = pnlPct >= 0 ? '#1E3829' : '#381E29';
  const badgeTxtColor = pnlPct >= 0 ? C.success : C.danger;
  
  const strategy = status?.active_strategy ?? 'Thunder';
  const session = status?.open_markets?.length > 0 ? (status.open_markets.includes('EURUSD') ? 'London' : 'New York') : 'London';
  const pairs = status?.markets_analyzed?.join(', ') || 'EUR/USD, XAUUSD, GBPUSD';

  return (
    <View style={[s.safe, { paddingTop: Platform.OS === 'android' ? 0 : 20 }]}>
      <StatusBar style="light" translucent={true} backgroundColor="transparent" />
      <ScrollView
        style={s.screen} contentContainerStyle={s.scrollDash}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.warning} />}
      >
        <View style={s.splitRow}>
          <View style={{ flex: 1 }}>
            <Text style={s.secLbl}>Balance</Text>
            <Text style={s.dashBigVal}>{fmt$(balance)}</Text>
            <View style={s.badgeWrap}>
              <View style={[s.badgeDanger, { backgroundColor: badgeColor }]}>
                <Text style={[s.badgeTxt, { color: badgeTxtColor }]}>{pnlPctArrow} {pnlPctDisp}%</Text>
              </View>
              <Text style={s.todayTxt}>{pnlTodayStr} Today</Text>
              </View>
          </View>
          <View style={{ flex: 1, paddingLeft: 10 }}>
            <Text style={s.secLbl}>Equity</Text>
            <Text style={s.dashBigVal}>{fmt$(equity)}</Text>
            <View style={s.badgeWrap}>
              <View style={[s.badgeDanger, { backgroundColor: badgeColor }]}>
                <Text style={[s.badgeTxt, { color: badgeTxtColor }]}>{pnlPctArrow} {pnlPctDisp}%</Text>
              </View>
              <Text style={s.todayTxt}>{pnlTodayStr} Today</Text>
              </View>
          </View>
        </View>

        <View style={[s.splitRow, { marginTop: 8 }]}>
          <View style={{ flex: 1 }}>
            <Text style={s.secLbl}>Open P & L</Text>
            <Text style={[s.dashHugeVal, { color: profit >= 0 ? C.success : C.danger }]}>
              {profit >= 0 ? '+' : ''}{fmt$(profit)}
            </Text>
          </View>
          <View style={{ flex: 1, paddingLeft: 10 }}>
            <Text style={s.secLbl}>Positions</Text>
            <Text style={[s.dashHugeVal, { color: '#FFF' }]}>{String(positions)}</Text>
          </View>
        </View>

        <View style={s.outlineCard}>
          <View style={s.outlineRow}>
            <Text style={s.outlineLbl}>Strategy</Text>
            <Text style={s.outlineVal}>{strategy.charAt(0).toUpperCase() + strategy.slice(1)}</Text>
          </View>
          <View style={s.divLine} />
          <View style={s.outlineRow}>
            <Text style={s.outlineLbl}>Open Session</Text>
            <Text style={s.outlineVal}>{session}</Text>
          </View>
          <View style={s.divLine} />
          <View style={s.outlineRow}>
            <Text style={s.outlineLbl}>Trading Pairs</Text>
            <Text style={s.outlineVal}>{pairs}</Text>
          </View>
          <View style={s.divLine} />
          <View style={s.outlineRow}>
            <Text style={s.outlineLbl}>Last Updated</Text>
            <Text style={s.outlineVal}>{status?.last_updated ? fmtTime(status.last_updated) : '13:42 PM'}</Text>
          </View>
        </View>

        <View style={s.btnRow}>
          <TouchableOpacity
            style={[s.halfBtn, { marginRight: 12 }, !isManual ? { backgroundColor: C.warning } : { backgroundColor: '#3B4151' }]}
            onPress={() => toggleMode(false)}
          >
            <Text style={[s.halfBtnTxt, !isManual ? { color: '#FFF' } : { color: C.secondary }]}>AUTO</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[s.halfBtn, isManual ? { backgroundColor: C.manualBtn } : { backgroundColor: '#3B4151' }]}
            onPress={() => toggleMode(true)}
          >
            <Text style={[s.halfBtnTxt, isManual ? { color: '#FFF' } : { color: C.secondary }]}>MANUAL</Text>
          </TouchableOpacity>
        </View>

        {isPaused ? <View style={s.warnBand}><Text style={s.warnBandTxt}>Daily loss limit hit</Text></View> : null}

        <View style={s.btnRow}>
          <TouchableOpacity
            style={[s.actionStop, { flex: 1 }]}
            onPress={() => sendCommand('stop')}
          >
            <Text style={s.actionTxt}>STOP</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[s.actionStart, { flex: 1, marginLeft: 16 }]}
            onPress={() => sendCommand('start')}
          >
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
  const [signals, setSignals] = useState({});
  const [expandedIds, setExpandedIds] = useState({});

  useEffect(() => {
    const u = onValue(ref(db, `users/${USER_ID}/pending_signals`), s => setSignals(s.val() ?? {}));
    return () => u();
  }, []);

  const respond = useCallback(async (key, response) => {
    await update(ref(db, `users/${USER_ID}/pending_signals/${key}`), {
      status: response, responded_at: new Date().toISOString(),
    });
  }, []);

  const toggleExpand = useCallback((key) => {
    setExpandedIds(prev => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const data = useMemo(() => {
    const all = Object.entries(signals);
    const pending = all
      .filter(([, v]) => v.status === 'PENDING')
      .sort(([, a], [, b]) => b.pushed_at > a.pushed_at ? 1 : -1);
    const history = all
      .filter(([, v]) => v.status !== 'PENDING')
      .sort(([, a], [, b]) => b.pushed_at > a.pushed_at ? 1 : -1)
      .slice(0, 15);
    return [...pending, ...history];
  }, [signals]);

  const renderItem = useCallback(({ item }) => {
    const [key, sig] = item;
    return (
      <SignalCard 
        sigKey={key} 
        sig={sig} 
        isExpanded={expandedIds[key]} 
        onToggle={toggleExpand} 
        onRespond={respond} 
      />
    );
  }, [expandedIds, toggleExpand, respond]);

  return (
    <View style={[s.safe, { paddingTop: Platform.OS === 'android' ? 0 : 20 }]}>
      <StatusBar style="light" translucent={true} backgroundColor="transparent" />
      <View style={s.screen}>
        {data.length === 0 ? (
          <Text style={{ color: C.secondary, textAlign: 'center', marginTop: 50 }}>No pending signals or history.</Text>
        ) : (
          <FlashList
            data={data}
            renderItem={renderItem}
            keyExtractor={item => item[0]}
            estimatedItemSize={100}
            contentContainerStyle={[s.scrollList, { paddingBottom: 100 }]}
            showsVerticalScrollIndicator={false}
          />
        )}
      </View>
    </View>
  );
});

// ════════════════════════════════════════════════════════════════════
// INSIGHTS SCREEN
// ════════════════════════════════════════════════════════════════════
const InsightsScreen = memo(function InsightsScreen() {
  const [sentiment, setSentiment] = useState({});

  useEffect(() => {
    const u = onValue(ref(db, `users/${USER_ID}/sentiment`), s => setSentiment(s.val() ?? {}));
    return () => u();
  }, []);

  const fmtT = useCallback((isoString) => {
    if (!isoString) return '--:--';
    const d = new Date(isoString);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }, []);

  const PAIRS = useMemo(() => ['EURUSD', 'GBPUSD', 'XAUUSD'], []);

  return (
    <View style={[s.safe, { paddingTop: Platform.OS === 'android' ? 0 : 20 }]}>
      <StatusBar style="light" translucent={true} backgroundColor="transparent" />
      <ScrollView style={s.screen} contentContainerStyle={[s.scrollList, { paddingBottom: 100 }]}>
        {PAIRS.map((pair, i) => {
          const data = sentiment[pair] || {};
          const score = data.score ?? 0;
          const buyPct = Math.round(((score + 1) / 2) * 100);
          const sellPct = 100 - buyPct;
          const isBull = score >= 0;
          const trendDisp = `${isBull ? '+' : ''}${(score * 12.5).toFixed(1)}% (24h) ${isBull ? '▲' : '▼'}`;
          
          return (
            <View key={i} style={s.insightGroup}>
              <View style={s.dragInd} />
              <View style={s.trendHead}>
                <View>
                  <Text style={s.trendSub}>BEARISH</Text>
                  <Text style={[s.trendMain, { color: C.danger }]}>{sellPct}% Sell</Text>
                </View>
                <View style={{ alignItems: 'center' }}>
                  <Text style={s.trendTitle}>Current Trend</Text>
                  <Text style={[s.trendAlert, { color: isBull ? C.success : C.danger }]}>{trendDisp}</Text>
                  <Text style={{ color: C.secondary, fontSize: 11, marginTop: 4 }}>Updated: {fmtT(data.updated_at)}</Text>
                </View>
                <View style={{ alignItems: 'flex-end' }}>
                  <Text style={s.trendSub}>BULLISH</Text>
                  <Text style={[s.trendMain, { color: C.success }]}>{buyPct}% Buy</Text>
                </View>
              </View>

              <View style={s.trendLinesBg}>
                <View style={[s.trendLineS, { width: `${sellPct}%` }]} />
                <View style={[s.trendLineB, { width: `${buyPct}%` }]} />
              </View>

              <View style={s.divLineList} />

              <View style={s.sigCardInner}>
                <View style={s.sigLeft}>
                  <PairIcon symbol={pair} size={42} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={s.sigTitle}>{pair}</Text>
                  <Text style={s.sigSub}>{getMockPairNames(pair) || 'Euro / U.S. Dollar'}</Text>
                </View>
                <View style={{ alignItems: 'flex-end' }}>
                  <Text style={[s.sigDir, { color: isBull ? C.success : C.danger }]}>{isBull ? 'BUY' : 'SELL'}</Text>
                  <Text style={s.sigSlTp}>Conf: {data.confidence ?? '—'}</Text>
                </View>
              </View>

              {(data.gpt4_summary || data.grok_summary || data.risk_advisory) && (
                <View style={s.gptBox}>
                  <Text style={s.gptTitle}>Source: {data.source || 'Market Data'}</Text>
                  {data.gpt4_summary && <Text style={s.gptText}>• {data.gpt4_summary}</Text>}
                  {data.grok_summary && <Text style={s.gptText}>• {data.grok_summary}</Text>}
                  {data.risk_advisory && <Text style={[s.gptText, { color: C.warning }]}>• {data.risk_advisory}</Text>}
                </View>
              )}
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
  const [alerts, setAlerts] = useState({});
  useEffect(() => {
    const u = onValue(ref(db, `users/${USER_ID}/alerts`), s => setAlerts(s.val() ?? {}));
    return () => u();
  }, []);

  const data = useMemo(() => {
    return Object.entries(alerts)
      .sort(([, a], [, b]) => b.sent_at > a.sent_at ? 1 : -1)
      .slice(0, 30);
  }, [alerts]);

  const renderItem = useCallback(({ item }) => {
    const [key, sig] = item;
    const isBuy = sig.direction === 'BUY';
    const subText = getMockPairNames(sig.symbol) || 'Data Item';
    const isExecuted = sig.alert_type === 'TRADE_EXECUTED';
    return (
      <View style={s.sigCard}>
        <View style={s.sigLeft}>
          <PairIcon symbol={sig.symbol} size={42} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={s.sigTitle}>{sig.symbol}</Text>
          <Text style={s.sigSub}>{isExecuted ? 'Trade Executed' : subText}</Text>
          {isExecuted && (
            <Text style={[s.sigSlTp, { color: C.secondary, marginTop: 2 }]}>
              Ticket: #{sig.ticket} | Price: {sig.filled_price}
            </Text>
          )}
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <Text style={[s.sigDir, { color: isBuy ? C.success : C.danger }]}>{sig.direction}</Text>
          {!isExecuted ? (
            <Text style={s.sigSlTp}>SL: {sig.suggested_sl}, TP: {sig.suggested_tp}</Text>
          ) : (
            <Text style={s.sigSlTp}>{sig.strategy || 'Thunder'}</Text>
          )}
        </View>
      </View>
    );
  }, []);

  return (
    <View style={[s.safe, { paddingTop: Platform.OS === 'android' ? 0 : 20 }]}>
      <StatusBar style="light" translucent={true} backgroundColor="transparent" />
      <View style={s.screen}>
        <FlashList
          data={data}
          renderItem={renderItem}
          keyExtractor={item => item[0]}
          estimatedItemSize={80}
          contentContainerStyle={[s.scrollList, { paddingBottom: 100 }]}
          showsVerticalScrollIndicator={false}
        />
      </View>
    </View>
  );
});

// ════════════════════════════════════════════════════════════════════
// SETTINGS SCREEN
// ════════════════════════════════════════════════════════════════════
const SettingsScreen = memo(function SettingsScreen() {
  const [braveConfig, setBraveConfig] = useState(null);
  const [health, setHealth] = useState(null);
  const [expanded, setExpanded] = useState({ thunder: true, frost: false, flow: false });

  useEffect(() => {
    const u1 = onValue(ref(db, `users/${USER_ID}/brave_config`), s => setBraveConfig(s.val()));
    const u2 = onValue(ref(db, `users/${USER_ID}/health`), s => setHealth(s.val()));
    return () => { u1(); u2(); };
  }, []);

  const stratCfg = useMemo(() => braveConfig?.strategy_config ?? {
    thunder: { enabled: true, max_trades: 2 },
    frost: { enabled: false, max_trades: 2 },
    flow: { enabled: false, max_trades: 2 },
  }, [braveConfig]);

  const toggleStrategy = useCallback(async (name) => {
    const updates = {};
    Object.keys(stratCfg).forEach(sName => {
      updates[`strategy_config/${sName}/enabled`] = (sName === name);
    });
    updates['active_strategy'] = name;
    await update(ref(db, `users/${USER_ID}/brave_config`), updates);
  }, [stratCfg]);

  const setMaxTrades = useCallback(async (name, val) => {
    await update(ref(db, `users/${USER_ID}/brave_config/strategy_config/${name}`), {
      max_trades: val,
    });
  }, []);

  const STRAT_INFO = useMemo(() => ({
    thunder: { label: 'Thunder', detail: 'Breakout:', detailVal: 'London+NY' },
    frost: { label: 'Frost', detail: null, detailVal: null },
    flow: { label: 'Flow', detail: null, detailVal: null },
  }), []);

  return (
    <View style={[s.safe, { paddingTop: Platform.OS === 'android' ? 0 : 20 }]}>
      <StatusBar style="light" translucent={true} backgroundColor="transparent" />
      <ScrollView style={s.screen} contentContainerStyle={[s.scrollList, { paddingBottom: 100 }]}>
        {['thunder', 'frost', 'flow'].map(name => {
          const info = STRAT_INFO[name];
          const cfg = stratCfg[name] ?? { enabled: false, max_trades: 2 };
          const enabled = cfg.enabled === true;
          const isOpen = expanded[name];

          return (
            <View key={name} style={s.setCard}>
              <TouchableOpacity
                style={s.setRow}
                onPress={() => setExpanded(e => ({ ...e, [name]: !e[name] }))}
              >
                <Text style={s.setLabel}>{info.label}</Text>
                <MaterialCommunityIcons name={isOpen ? 'chevron-down' : 'chevron-right'} size={24} color={C.secondary} />
              </TouchableOpacity>

              {isOpen ? (
                <View>
                  {info.detail && (
                    <View style={s.setRow}>
                      <Text style={s.setKey}>{info.detail}</Text>
                      <Text style={s.setValStr}>{info.detailVal}</Text>
                    </View>
                  )}
                  <View style={s.setRow}>
                    <Text style={s.setKey}>Number of trade:</Text>
                    <Text style={s.setValStr}>{String(cfg.max_trades ?? 2)}</Text>
                  </View>
                  <View style={s.setRow}>
                    <Text style={s.setKey}>Active:</Text>
                    <Text style={s.setValStr}>{enabled ? 'Yes' : 'No'}</Text>
                  </View>
                </View>
              ) : (
                <View style={s.setRow}>
                  <Text style={s.setKey}>Active</Text>
                  <TouchableOpacity
                    style={[s.toggleOff, enabled && { backgroundColor: C.success }]}
                    onPress={() => toggleStrategy(name)}
                  >
                    <View style={[s.toggleThumbOff, enabled && { alignSelf: 'flex-end', backgroundColor: '#FFF' }]} />
                  </TouchableOpacity>
                </View>
              )}
            </View>
          );
        })}

        <View style={s.setCard}>
           <View style={s.setRow}>
             <Text style={s.setKeyB}>Status</Text>
             <Text style={s.setValMuted}>{health?.status ?? '—'}</Text>
           </View>
           <View style={s.setRow}>
             <Text style={s.setKeyB}>MT5 Connected</Text>
             <Text style={s.setValMuted}>{health?.mt5_connected === true ? 'Yes' : 'No'}</Text>
           </View>
           <View style={s.setRow}>
             <Text style={s.setKeyB}>Account Trading</Text>
             <Text style={s.setValMuted}>{health?.account_trade_allowed === true ? 'Allowed' : 'Restricted'}</Text>
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
// TAB NAVIGATOR
// ════════════════════════════════════════════════════════════════════
export default function App() {
  const [dbReady, setDbReady] = useState(false);

  useEffect(() => {
    // Hide splash screen when component mounts
    const hideSplash = async () => {
      await SplashScreen.hideAsync();
      setDbReady(true);
    };
    hideSplash();
  }, []);

  return (
    <View style={{ flex: 1, backgroundColor: '#000000' }}>
      <NavigationContainer>
        <Tab.Navigator
        screenOptions={({ route }) => ({
          headerShown: false,
          tabBarStyle: {
            position: 'absolute',
            backgroundColor: C.tabBg,
            bottom: 24,
            left: 20,
            right: 20,
            height: 72,
            borderRadius: 24,
            borderTopWidth: 0,
            paddingBottom: 0,
            elevation: 10,
            shadowColor: '#000',
            shadowOffset: { width: 0, height: 10 },
            shadowOpacity: 0.6,
            shadowRadius: 10,
          },
          tabBarActiveTintColor: C.tabActive,
          tabBarInactiveTintColor: C.tabInactive,
          tabBarLabelStyle: {
            fontSize: 11,
            fontWeight: '600',
            paddingBottom: 14,
            marginTop: -6,
          },
          tabBarIcon: ({ focused, color }) => {
            let iconName;
            if (route.name === 'Dashboard') iconName = 'poll';
            else if (route.name === 'Signals') iconName = 'fire';
            else if (route.name === 'Insights') iconName = 'chart-timeline-variant';
            else if (route.name === 'Alerts') iconName = 'alert-outline';
            else if (route.name === 'Settings') iconName = 'cog-outline';

            return (
              <View style={{ alignItems: 'center', width: '100%', height: '100%', paddingTop: 16 }}>
                {focused && (
                  <View style={{
                    position: 'absolute', top: 0, width: 22, height: 3,
                    backgroundColor: '#FFF', borderRadius: 2
                  }} />
                )}
                <MaterialCommunityIcons name={iconName} size={28} color={color} style={{ opacity: focused ? 1 : 0.8, marginBottom: 4 }} />
              </View>
            );
          },
        })}
      >
        <Tab.Screen name="Dashboard" component={DashboardScreen} />
        <Tab.Screen name="Signals" component={SignalsScreen} />
        <Tab.Screen name="Insights" component={InsightsScreen} />
        <Tab.Screen name="Alerts" component={AlertsScreen} />
        <Tab.Screen name="Settings" component={SettingsScreen} />
      </Tab.Navigator>
      </NavigationContainer>
    </View>
  );
}

// ════════════════════════════════════════════════════════════════════
// STYLES
// ════════════════════════════════════════════════════════════════════
const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.bg },
  screen: { flex: 1, backgroundColor: C.bg },
  scrollDash: { paddingHorizontal: 16, paddingTop: 30, paddingBottom: 120 },
  scrollList: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 120 },

  // Dashboard blocks
  splitRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 24 },
  secLbl: { fontSize: 14, color: C.secondary, marginBottom: 8 },
  dashBigVal: { fontSize: 36, fontWeight: '700', color: C.primary, marginBottom: 12 },
  dashHugeVal: { fontSize: 44, fontWeight: '700', marginBottom: 4 },
  badgeWrap: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  badgeDanger: { backgroundColor: '#381E29', borderRadius: 4, paddingHorizontal: 6, paddingVertical: 4 },
  badgeTxt: { fontSize: 11, color: C.danger, fontWeight: '700' },
  todayTxt: { fontSize: 13, color: '#DCE0E8' },

  outlineCard: {
    borderWidth: 1, borderColor: '#262A35', borderRadius: 12,
    paddingHorizontal: 16, paddingVertical: 10, marginBottom: 26,
    backgroundColor: 'transparent'
  },
  outlineRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 12 },
  outlineLbl: { color: C.secondary, fontSize: 14 },
  outlineVal: { color: C.primary, fontSize: 14, fontWeight: '700' },
  divLine: { height: 1, backgroundColor: '#262A35' },

  btnRow: { flexDirection: 'row', marginBottom: 26 },
  halfBtn: { flex: 1, height: 50, borderRadius: 10, justifyContent: 'center', alignItems: 'center' },
  halfBtnTxt: { fontSize: 15, fontWeight: '700', letterSpacing: 0.5 },

  trendOuter: { backgroundColor: '#161923', borderRadius: 16, padding: 16, marginBottom: 20 },
  trendHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 },
  trendSub: { color: C.secondary, fontSize: 12, marginBottom: 4 },
  trendMain: { fontSize: 13, fontWeight: '700' },
  trendTitle: { color: C.primary, fontSize: 14, fontWeight: '700', marginBottom: 4 },
  trendAlert: { fontSize: 13, fontWeight: '600' },
  trendLinesBg: { flexDirection: 'row', height: 4, gap: 6 },
  trendLineS: { backgroundColor: C.danger, borderRadius: 2 },
  trendLineB: { backgroundColor: C.success, borderRadius: 2 },

  actionStop: {
    backgroundColor: '#DD4658', borderRadius: 12, height: 56,
    justifyContent: 'center', alignItems: 'center',
    shadowColor: '#DD4658', shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.4, shadowRadius: 10, elevation: 8
  },
  actionStart: {
    backgroundColor: '#1ECA8E', borderRadius: 12, height: 56,
    justifyContent: 'center', alignItems: 'center',
    shadowColor: '#1ECA8E', shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.4, shadowRadius: 10, elevation: 8
  },
  actionTxt: { color: '#FFF', fontSize: 16, fontWeight: '700', letterSpacing: 0.5 },

  // List Cards (Signals, Alerts, Insights pair part)
  sigCard: {
    flexDirection: 'row', backgroundColor: '#141822', borderWidth: 1, borderColor: '#232832',
    borderRadius: 14, padding: 14, marginBottom: 12, alignItems: 'center'
  },
  sigCardCol: {
    flexDirection: 'column', backgroundColor: '#141822', borderWidth: 1, borderColor: '#232832',
    borderRadius: 14, padding: 14, marginBottom: 12, alignItems: 'stretch'
  },
  sigCardTop: { flexDirection: 'row', alignItems: 'center' },
  sigExpanded: { marginTop: 16, paddingTop: 16, borderTopWidth: 1, borderTopColor: '#232832' },
  sigBtnRow: { flexDirection: 'row', justifyContent: 'space-between', gap: 12 },
  sigAcceptBtn: { flex: 1, backgroundColor: C.warning, paddingVertical: 12, borderRadius: 8, alignItems: 'center' },
  sigAcceptBtnTxt: { color: '#FFF', fontWeight: '700', fontSize: 14 },
  sigRejectBtn: { flex: 1, backgroundColor: C.manualBtn, paddingVertical: 12, borderRadius: 8, alignItems: 'center' },
  sigRejectBtnTxt: { color: '#FFF', fontWeight: '700', fontSize: 14 },
  
  sigCardInner: { flexDirection: 'row', alignItems: 'center' },
  sigLeft: { marginRight: 14 },
  sigTitle: { color: C.primary, fontSize: 16, fontWeight: '700', marginBottom: 2 },
  sigSub: { color: C.secondary, fontSize: 13 },
  sigDir: { fontSize: 14, fontWeight: '700', marginBottom: 4 },
  sigSlTp: { color: '#DCE0E8', fontSize: 12 },

  // Settings
  setCard: { backgroundColor: '#141822', borderRadius: 16, marginBottom: 12, paddingVertical: 4 },
  setRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 20, paddingVertical: 18 },
  setLabel: { color: C.primary, fontSize: 15, fontWeight: '700' },
  setKey: { color: C.primary, fontSize: 15, fontWeight: '700' },
  setValStr: { color: C.primary, fontSize: 15, fontWeight: '700' },
  setKeyB: { color: C.primary, fontSize: 15, fontWeight: '700' },
  setValMuted: { color: '#A0A7B6', fontSize: 14 },

  toggleOff: { width: 44, height: 26, borderRadius: 13, backgroundColor: '#393C48', justifyContent: 'center', paddingHorizontal: 3 },
  toggleThumbOff: { width: 20, height: 20, borderRadius: 10, backgroundColor: '#FFF' },

  // Insight grouping block
  insightGroup: { backgroundColor: '#141822', borderRadius: 16, padding: 18, marginBottom: 24, borderWidth: 1, borderColor: '#212630' },
  dragInd: { width: 36, height: 4, backgroundColor: '#394154', borderRadius: 2, alignSelf: 'center', marginBottom: 18 },
  divLineList: { height: 1, backgroundColor: '#212630', marginVertical: 16, marginHorizontal: -18 },
  gptBox: { marginTop: 16, borderWidth: 1, borderColor: '#212630', borderRadius: 10, padding: 16, backgroundColor: '#191E2A' },
  gptTitle: { color: C.primary, fontSize: 14, fontWeight: '700', marginBottom: 12 },
  gptText: { color: '#B3B9C5', fontSize: 13, lineHeight: 22, marginBottom: 8 },

  warnBand: { backgroundColor: 'rgba(255,75,92,0.1)', borderLeftWidth: 3, borderLeftColor: C.danger, padding: 10, marginBottom: 12 },
  warnBandTxt: { fontSize: 12, color: C.danger },
});