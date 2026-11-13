import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { I18nProvider } from './i18n';
import { ThemeProvider } from './lib/theme';
import { AuthProvider } from './lib/auth';
import { Download, Landing, NotFound, Pricing } from './pages/Public';
import { AuthCallback, Invite, SignIn, SignUp, SignupFinish } from './pages/Auth';
import { RequireAuth, TenantProvider } from './components/AppShell';
import { Dashboard } from './pages/Dashboard';
import { Devices } from './pages/Devices';
import { Groups } from './pages/Groups';
import { Deploy } from './pages/Deploy';
import { Team } from './pages/Team';
import { Settings } from './pages/Settings';
import { Account, Billing } from './pages/Account';
import { Status } from './pages/Status';

export function App() {
  return (
    <ThemeProvider>
      <I18nProvider>
        <AuthProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<Landing />} />
              <Route path="/pricing" element={<Pricing />} />
              <Route path="/download" element={<Download />} />
              <Route path="/status" element={<Status />} />
              <Route path="/signin" element={<SignIn />} />
              <Route path="/signup" element={<SignUp />} />
              <Route path="/signup/finish" element={<SignupFinish />} />
              <Route path="/auth/callback" element={<AuthCallback />} />
              <Route path="/invite" element={<Invite />} />
              <Route path="/app" element={<RequireAuth><TenantProvider /></RequireAuth>}>
                <Route index element={<Dashboard />} />
                <Route path="devices" element={<Devices />} />
                <Route path="groups" element={<Groups />} />
                <Route path="deploy" element={<Deploy />} />
                <Route path="team" element={<Team />} />
                <Route path="settings" element={<Settings />} />
                <Route path="billing" element={<Billing />} />
                <Route path="account" element={<Account />} />
              </Route>
              <Route path="*" element={<NotFound />} />
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </I18nProvider>
    </ThemeProvider>
  );
}
