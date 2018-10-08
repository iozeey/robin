import { applyMiddleware, createStore, combineReducers, compose } from 'redux';
import createHistory from 'history/createBrowserHistory';
import { routerMiddleware, routerReducer } from 'react-router-redux';
import createSagaMiddleware from 'redux-saga';

import rootSaga from 'src/sagas';
import apiReducer from 'src/reducers/api';
import symbolSearchReducer from 'src/reducers/symbolSearch';

const reducers = {
  router: routerReducer,
  api: apiReducer,
  symbolSearch: symbolSearchReducer,
};

export const history = createHistory();

const createdRouterMiddleware = routerMiddleware(history);
const sagaMiddleware = createSagaMiddleware();

const composeEnhancers = window.__REDUX_DEVTOOLS_EXTENSION_COMPOSE__ || compose;

export const store = createStore(
  combineReducers(reducers),
  composeEnhancers(applyMiddleware(createdRouterMiddleware, sagaMiddleware))
);

sagaMiddleware.run(rootSaga);
