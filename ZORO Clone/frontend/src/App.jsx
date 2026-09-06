import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { CartProvider } from "./context/CartContext";
import { CompareProvider } from "./context/CompareContext";
import Header from "./components/Header";
import Footer from "./components/Footer";
import CartToast from "./components/CartToast";
import CompareTray from "./components/CompareTray";
import HomePage from "./pages/HomePage";
import ProductPage from "./pages/ProductPage";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import CartPage from "./pages/CartPage";
import CheckoutPage from "./pages/CheckoutPage";
import OrderConfirmationPage from "./pages/OrderConfirmationPage";
import BulkOrderPage from "./pages/BulkOrderPage";
import AccountPage from "./pages/AccountPage";
import AboutPage from "./pages/AboutPage";
import ContactPage from "./pages/ContactPage";
import ShippingPage from "./pages/ShippingPage";
import ReturnsPage from "./pages/ReturnsPage";
import AdminLayout from "./pages/admin/AdminLayout";
import AdminDashboard from "./pages/admin/AdminDashboard";
import AdminOrders from "./pages/admin/AdminOrders";
import AdminCases from "./pages/admin/AdminCases";
import AdminProducts from "./pages/admin/AdminProducts";
import AdminCustomers from "./pages/admin/AdminCustomers";
import AdminEmails from "./pages/admin/AdminEmails";
import "./index.css";

function App() {
  return (
    <AuthProvider>
      <CartProvider>
        <CompareProvider>
          <BrowserRouter>
            <Header />
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/search" element={<HomePage />} />
              <Route path="/product/:zoroCode" element={<ProductPage />} />
              <Route path="/login" element={<LoginPage />} />
              <Route path="/register" element={<RegisterPage />} />
              <Route path="/cart" element={<CartPage />} />
              <Route path="/checkout" element={<CheckoutPage />} />
              <Route path="/order-confirmation/:orderId" element={<OrderConfirmationPage />} />
              <Route path="/bulk-order" element={<BulkOrderPage />} />
              <Route path="/account" element={<AccountPage />} />
              <Route path="/about" element={<AboutPage />} />
              <Route path="/contact" element={<ContactPage />} />
              <Route path="/shipping" element={<ShippingPage />} />
              <Route path="/returns" element={<ReturnsPage />} />
              <Route path="/admin" element={<AdminLayout />}>
                <Route index element={<AdminDashboard />} />
                <Route path="orders" element={<AdminOrders />} />
                <Route path="cases" element={<AdminCases />} />
                <Route path="products" element={<AdminProducts />} />
                <Route path="customers" element={<AdminCustomers />} />
                <Route path="emails" element={<AdminEmails />} />
              </Route>
            </Routes>
            <Footer />
            <CartToast />
            <CompareTray />
          </BrowserRouter>
        </CompareProvider>
      </CartProvider>
    </AuthProvider>
  );
}

export default App;
